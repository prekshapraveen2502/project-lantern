"""Part 5 item 1: the Pydantic DealProfile (brief Part 5: overview; business model; revenue/EBITDA history; KPIs;
customers and concentration; management; stated add-backs; process terms; disclosure gaps).

Two sets of models:
- the extraction models (extraction_model(), returned by the LLM through src/llm/client.py): numbers come back as
  written, with a scale word and a unit kind, plus a verbatim quote (Part 1 rule, DECISIONS 2026-11-06 C7-C10);
- the stored DealProfile: code converts each number to base units, locates its quote in one CIM block (page,
  block_id) and sets deal_id from the data-room folder, never from the model.
Every field may be null: null means the source does not state it.

flatten() turns a profile into the field ids the gold file uses (Phase A section 3), e.g. history.revenue.FY2025.
"""
import re
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, create_model

from src.facts.claims import COMPARATORS, CY_RE, FY_RE, Q_RE, QUALIFIERS, SCALES, locate

UNIT_KINDS = ("USD", "percent", "fraction", "count", "months")
Strategy = Literal["single", "staged", "staged_reconciled"]
# Fixed (non-row) numeric fields -> their metrics_vocab name, used to link a field to a Part 1 claim or a tier-3
# fact. Names follow the vocabulary's synonyms ("employees" -> headcount, "recurring revenue share" ->
# subscription_share); contract length has no vocabulary metric.
FIELD_METRIC = {
    "overview.employees": "headcount",
    "business_model.contract_length": None,
    "business_model.recurring_revenue_share": "subscription_share",
    "customers.top1_customer_share": "top1_customer_share",
    "customers.top10_customer_share": "top10_customer_share",
    "customers.customer_count": "active_customers",
    "addbacks.reported_ebitda": "reported_ebitda",
    "addbacks.management_adjusted_ebitda": "management_adjusted_ebitda",
}


# ---------------------------------------------------------------- stored profile
class Source(BaseModel):
    doc_id: str
    page: Optional[int]
    block_id: str
    quote: str
    # exact: the quote occurs verbatim (whitespace-insensitive) in the block; in_order: its tokens occur in order in
    # the block with other tokens between (table cells: label, other columns, value; decision 2026-11-07 Q14 a). An
    # in_order match guarantees the block contains the value, not that the value sits on the quoted label (it can
    # match across table rows). none: not located; only in the raw profiles scored before the locate step.
    match: Literal["exact", "in_order", "none"] = "exact"


class Reconciled(BaseModel):
    """Strategy (c): the Part 1 reconcile result that replaced this value (decision 2026-11-07 Q3 a)."""
    claim_id: str
    status: str
    fact_id: Optional[str]
    gap: Optional[float]
    claimed_value: float
    claimed_comparator: str


class Num(BaseModel):
    value: float                                   # base units: USD, a fraction for percents, counts as-is
    unit: Literal["USD", "pure", "count", "months"]
    comparator: Literal[COMPARATORS] = "eq"
    as_written: str
    period: Optional[str] = None                   # FYyyyy | CYyyyy | "Qn FYyyyy"
    period_assumed: bool = False                   # no period stated -> the deal's latest fiscal year (Part 1 rule)
    qualifier: list[Literal[QUALIFIERS]] = []
    sign_flipped: bool = False                     # a sign: cost metric stored positive (DECISIONS 2026-11-05)
    source: Source
    ref: Optional[str] = None                      # claim_id, fact_id or calc_id this value comes from
    reconcile: Optional[Reconciled] = None


class Overview(BaseModel):
    company_name: Optional[str] = None
    codename: Optional[str] = None
    hq_city: Optional[str] = None
    hq_state: Optional[str] = None
    founded_year: Optional[int] = None
    employees: Optional[Num] = None
    end_market: Optional[str] = None
    description: Optional[str] = None              # free text, not scored


class Stream(BaseModel):
    name: str
    pricing_basis: Optional[str] = None


class BusinessModel(BaseModel):
    revenue_streams: list[Stream] = []
    sales_channels: list[str] = []
    sponsor_bank: Optional[str] = None
    contract_length: Optional[Num] = None          # unit months
    recurring_revenue_share: Optional[Num] = None


class Line(BaseModel):
    label: str                                     # as labelled in the source ("Revenue")
    metric: str                                    # a config/metrics_vocab.yaml name
    value: Num


class History(BaseModel):
    fiscal_year_end: Optional[str] = None          # "MM-DD"
    lines: list[Line] = []
    growth: list[Line] = []
    outlook: list[Line] = []                       # forward-looking or calendar-period figures


class Kpi(BaseModel):
    metric: str
    value: Num
    definition_note: Optional[str] = None


class Customers(BaseModel):
    top1_customer_share: Optional[Num] = None
    top10_customer_share: Optional[Num] = None
    customer_count: Optional[Num] = None
    customer_type: Optional[str] = None
    key_partners: list[str] = []


class Manager(BaseModel):
    name: str
    title: Optional[str] = None
    joined_year: Optional[int] = None
    background: Optional[str] = None               # free text, not scored


class AddBack(BaseModel):
    name: str
    amount: Num
    management_view: Optional[str] = None


class AddBacks(BaseModel):
    reported_ebitda: Optional[Num] = None
    management_adjusted_ebitda: Optional[Num] = None
    items: list[AddBack] = []


class Process(BaseModel):
    sell_side_adviser: Optional[str] = None
    transaction_type: Optional[str] = None
    bid_deadline: Optional[date] = None
    bid_type: Optional[str] = None
    memo_date: Optional[str] = None                # "YYYY-MM"


class DealProfile(BaseModel):
    deal_id: str
    strategy: Strategy
    overview: Overview = Overview()
    business_model: BusinessModel = BusinessModel()
    history: History = History()
    kpis: list[Kpi] = []
    customers: Customers = Customers()
    management: list[Manager] = []
    addbacks: AddBacks = AddBacks()
    process: Process = Process()
    disclosure_gaps: list[str] = []


# ---------------------------------------------------------------- extraction models (what the LLM returns)
def extraction_model(metric_names: list[str]) -> type[BaseModel]:
    """The response model for one extraction call; metric is an enum of the vocabulary's names."""
    Metric = Literal[tuple(sorted(metric_names))]
    ExNum = create_model(
        "ExNum",
        quote=(str, Field(description="the shortest clause containing the number, copied verbatim from one block")),
        as_written=(str, Field(description="the number exactly as written, e.g. $96.4m, 31%, a third")),
        number=(float, Field(description="the number as written: 96.4 for '$96.4m', 31 for '31%', 0.3333 for "
                                         "'a third', 410 for '410'")),
        scale_word=(Literal[tuple(SCALES)], Field(description="thousand / million / billion if written or stated "
                                                              "for the table (e.g. 'USD millions'), else none")),
        unit=(Literal[UNIT_KINDS], Field(description="USD for money, percent for 31%, fraction for 'a third', "
                                                     "count, months")),
        comparator=(Literal[COMPARATORS], Field(description="eq exact; approx for about/nearly; lt/le for less "
                                                            "than / does not exceed; gt/ge for over / at least")),
        period_label=(Optional[str], Field(description="FYyyyy, CYyyyy or 'Qn FYyyyy' if stated, else null")),
        qualifiers=(list[Literal[QUALIFIERS]], Field(description="adjusted, run-rate (annualized or x12, including "
                                                                 "when a footnote says so), pro forma, LTM, FY")),
    )
    N = Optional[ExNum]
    ExOverview = create_model(
        "ExOverview", company_name=(Optional[str], None), codename=(Optional[str], None),
        hq_city=(Optional[str], None), hq_state=(Optional[str], None),
        founded_year=(Optional[int], None), employees=(N, None),
        end_market=(Optional[str], Field(None, description="who the company sells to")),
        description=(Optional[str], Field(None, description="one sentence on what the company does")))
    ExStream = create_model("ExStream", name=(str, ...), pricing_basis=(Optional[str], None))
    ExBusinessModel = create_model(
        "ExBusinessModel", revenue_streams=(list[ExStream], []),
        sales_channels=(list[str], Field([], description="how the company sells, e.g. direct sales")),
        sponsor_bank=(Optional[str], None),
        contract_length=(N, Field(None, description="average contract length (unit months)")),
        recurring_revenue_share=(N, Field(None, description="share of revenue the source calls recurring")))
    ExLine = create_model(
        "ExLine", label=(str, Field(description="the line or metric as labelled in the source")),
        metric=(Metric, Field(description="the vocabulary metric this line is")), value=(ExNum, ...))
    ExHistory = create_model(
        "ExHistory",
        fiscal_year_end=(Optional[str], Field(None, description="fiscal year end as MM-DD, e.g. 06-30")),
        lines=(list[ExLine], Field([], description="revenue, cost and EBITDA lines by fiscal year")),
        growth=(list[ExLine], Field([], description="stated growth rates")),
        outlook=(list[ExLine], Field([], description="forecasts, expectations and calendar-period figures")))
    ExKpi = create_model("ExKpi", metric=(Metric, ...), value=(ExNum, ...),
                         definition_note=(Optional[str], Field(None, description="how the source defines it, "
                                                                                 "including footnotes")))
    ExCustomers = create_model(
        "ExCustomers",
        top1_customer_share=(N, Field(None, description="share of revenue of the largest customer or partner")),
        top10_customer_share=(N, Field(None, description="share of revenue of the top ten customers")),
        customer_count=(N, Field(None, description="number of customers")),
        customer_type=(Optional[str], None), key_partners=(list[str], []))
    ExManager = create_model("ExManager", name=(str, ...), title=(Optional[str], None),
                             joined_year=(Optional[int], Field(None, description="year they founded or joined the "
                                                                                 "company")),
                             background=(Optional[str], None))
    ExAddBack = create_model("ExAddBack", name=(str, ...), amount=(ExNum, ...),
                             management_view=(Optional[str], None))
    ExAddBacks = create_model(
        "ExAddBacks", reported_ebitda=(N, None),
        management_adjusted_ebitda=(N, Field(None, description="adjusted EBITDA as management presents it")),
        items=(list[ExAddBack], Field([], description="each add-back management states")))
    ExProcess = create_model(
        "ExProcess", sell_side_adviser=(Optional[str], None),
        transaction_type=(Optional[str], Field(None, description="e.g. majority recapitalization, sale")),
        bid_deadline=(Optional[str], Field(None, description="YYYY-MM-DD")),
        bid_type=(Optional[str], None),
        memo_date=(Optional[str], Field(None, description="date of the document as YYYY-MM")))
    return create_model(
        "ExProfile", overview=(ExOverview, ExOverview()), business_model=(ExBusinessModel, ExBusinessModel()),
        history=(ExHistory, ExHistory()), kpis=(list[ExKpi], []), customers=(ExCustomers, ExCustomers()),
        management=(list[ExManager], []), addbacks=(ExAddBacks, ExAddBacks()), process=(ExProcess, ExProcess()),
        disclosure_gaps=(list[str], Field([], description="items the document itself says are not disclosed, "
                                                          "one per item")))


# ---------------------------------------------------------------- extraction output -> stored profile
PERIOD_RES = (FY_RE, CY_RE, Q_RE)


def locate_in_order(quote: str, blocks: list[dict]) -> tuple[dict, str] | None:
    """(block, match kind): first the Part 1 rule (src/facts/claims.py locate, verbatim), else every token of the
    quote in order within one block's text, each token whole (bounded by whitespace), other tokens allowed
    between. Blocks are tried in order; the first match wins."""
    hit = locate(quote, blocks)
    if hit:
        return hit[0], "exact"
    tokens = quote.split()
    if not tokens:
        return None
    pat = re.compile(r"(?<!\S)" + r"(?:\s+\S+)*?\s+".join(map(re.escape, tokens)) + r"(?!\S)")
    for b in blocks:
        if pat.search(b["text"] or ""):
            return b, "in_order"
    return None


def to_value(number: float, scale_word: str, unit: str) -> tuple[float, str]:
    """(value in base units, stored unit)."""
    if unit == "percent":
        return number / 100.0, "pure"
    if unit == "fraction":
        return number, "pure"
    return number * 10 ** SCALES[scale_word], unit


class Builder:
    """Converts extraction output into stored models; everything dropped or assumed is logged. raw=True keeps numbers
    whose quote cannot be located (Source.match none): the raw profile scored before the locate step."""

    def __init__(self, deal_id: str, blocks: list[dict], vocab: dict, latest_fy: int, raw: bool = False):
        self.deal_id, self.blocks, self.vocab, self.latest_fy, self.raw = deal_id, blocks, vocab, latest_fy, raw
        self.log: list[dict] = []

    def num(self, x, where: str, metric: str | None = None) -> Optional[Num]:
        if x is None:
            return None
        hit = locate_in_order(x.quote, self.blocks)
        if hit is None:
            self.log.append({"field": where, "event": "quote_not_found", "quote": x.quote})
            if not self.raw:
                return None
            b, kind = {"doc_id": self.blocks[0]["doc_id"], "page": None, "block_id": ""}, "none"
        else:
            b, kind = hit
        value, unit = to_value(x.number, x.scale_word, x.unit)
        flipped = False
        if metric and (self.vocab["metrics"].get(metric) or {}).get("sign") == "cost" and value < 0:
            value, flipped = -value, True
        period, assumed = x.period_label, False
        if period is not None and not any(r.match(period) for r in PERIOD_RES):
            self.log.append({"field": where, "event": "period_unrecognised", "period": period})
            period = None
        if period is None:
            period, assumed = f"FY{self.latest_fy}", True
        return Num(value=value, unit=unit, comparator=x.comparator, as_written=x.as_written, period=period,
                   period_assumed=assumed, qualifier=list(dict.fromkeys(x.qualifiers)), sign_flipped=flipped,
                   source=Source(doc_id=b["doc_id"], page=b.get("page"), block_id=b["block_id"], quote=x.quote,
                                 match=kind))

    def line(self, x, where: str) -> Optional[Line]:
        v = self.num(x.value, f"{where}.{x.metric}", x.metric)
        return Line(label=x.label, metric=x.metric, value=v) if v else None

    def build(self, ex, strategy: str) -> DealProfile:
        o, bm, h, c, a, p = ex.overview, ex.business_model, ex.history, ex.customers, ex.addbacks, ex.process
        deadline = None
        if p.bid_deadline:
            try:
                deadline = date.fromisoformat(p.bid_deadline)
            except ValueError:
                self.log.append({"field": "process.bid_deadline", "event": "date_unrecognised",
                                 "value": p.bid_deadline})
        n = lambda x, f: self.num(x, f, FIELD_METRIC.get(f))
        return DealProfile(
            deal_id=self.deal_id, strategy=strategy,
            overview=Overview(company_name=o.company_name, codename=o.codename, hq_city=o.hq_city,
                              hq_state=o.hq_state, founded_year=o.founded_year,
                              employees=n(o.employees, "overview.employees"), end_market=o.end_market,
                              description=o.description),
            business_model=BusinessModel(
                revenue_streams=[Stream(name=s.name, pricing_basis=s.pricing_basis) for s in bm.revenue_streams],
                sales_channels=list(bm.sales_channels), sponsor_bank=bm.sponsor_bank,
                contract_length=n(bm.contract_length, "business_model.contract_length"),
                recurring_revenue_share=n(bm.recurring_revenue_share, "business_model.recurring_revenue_share")),
            history=History(fiscal_year_end=h.fiscal_year_end,
                            **{k: [x for x in (self.line(l, f"history.{k}") for l in getattr(h, k)) if x]
                               for k in ("lines", "growth", "outlook")}),
            kpis=[Kpi(metric=k.metric, value=v, definition_note=k.definition_note)
                  for k in ex.kpis if (v := self.num(k.value, f"kpis.{k.metric}", k.metric))],
            customers=Customers(top1_customer_share=n(c.top1_customer_share, "customers.top1_customer_share"),
                                top10_customer_share=n(c.top10_customer_share, "customers.top10_customer_share"),
                                customer_count=n(c.customer_count, "customers.customer_count"),
                                customer_type=c.customer_type, key_partners=list(c.key_partners)),
            management=[Manager(name=m.name, title=m.title, joined_year=m.joined_year, background=m.background)
                        for m in ex.management],
            addbacks=AddBacks(reported_ebitda=n(a.reported_ebitda, "addbacks.reported_ebitda"),
                              management_adjusted_ebitda=n(a.management_adjusted_ebitda,
                                                           "addbacks.management_adjusted_ebitda"),
                              items=[AddBack(name=i.name, amount=v, management_view=i.management_view)
                                     for i in a.items if (v := self.num(i.amount, f"addbacks.items.{i.name}"))]),
            process=Process(sell_side_adviser=p.sell_side_adviser, transaction_type=p.transaction_type,
                            bid_deadline=deadline, bid_type=p.bid_type, memo_date=p.memo_date),
            disclosure_gaps=list(p for p in ex.disclosure_gaps))


# ---------------------------------------------------------------- merging staged partial profiles
def _key(s: str) -> str:
    return " ".join(s.lower().split())


def _dedupe(items: list, key) -> list:
    seen, out = set(), []
    for x in items:
        k = key(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _dump(v):
    return v.model_dump(mode="json") if isinstance(v, BaseModel) else v


def merge(parts: list[DealProfile], strategy: str, log: list[dict]) -> DealProfile:
    """Staged extraction (decision 2026-11-07 Q4 a): scalars take the first non-null value in section order (a
    different later value is logged as a conflict); lists are joined, duplicates removed by their key."""
    out = DealProfile(deal_id=parts[0].deal_id, strategy=strategy)
    for area in ("overview", "business_model", "customers", "addbacks", "process", "history"):
        target = getattr(out, area)
        for name, field in type(target).model_fields.items():
            if field.annotation is not None and str(field.annotation).startswith("list"):
                continue
            for p in parts:
                v = getattr(getattr(p, area), name)
                if v is None:
                    continue
                cur = getattr(target, name)
                if cur is None:
                    setattr(target, name, v)
                elif _dump(cur) != _dump(v) and not (isinstance(cur, Num) and cur.value == v.value):
                    log.append({"field": f"{area}.{name}", "event": "merge_conflict", "kept": _dump(cur),
                                "dropped": _dump(v)})
    cat = lambda f: [x for p in parts for x in f(p)]
    line_key = lambda l: (l.metric, l.value.period)
    out.business_model.revenue_streams = _dedupe(cat(lambda p: p.business_model.revenue_streams), lambda s: _key(s.name))
    out.business_model.sales_channels = _dedupe(cat(lambda p: p.business_model.sales_channels), _key)
    for k in ("lines", "growth", "outlook"):
        setattr(out.history, k, _dedupe(cat(lambda p: getattr(p.history, k)), line_key))
    out.kpis = _dedupe(cat(lambda p: p.kpis), lambda k: (k.metric, k.value.period))
    out.customers.key_partners = _dedupe(cat(lambda p: p.customers.key_partners), _key)
    out.management = _dedupe(cat(lambda p: p.management), lambda m: _key(m.name))
    out.addbacks.items = _dedupe(cat(lambda p: p.addbacks.items), lambda i: _key(i.name))
    out.disclosure_gaps = _dedupe(cat(lambda p: p.disclosure_gaps), _key)
    return out


# ---------------------------------------------------------------- field ids (the gold file's ids)
def iter_nums(p: DealProfile):
    """(field id, metric or None, Num) for every number in the profile; the same ids as flatten()."""
    for f, metric in FIELD_METRIC.items():
        area, name = f.split(".")
        v = getattr(getattr(p, area), name)
        if v is not None:
            yield f, metric, v
    for k in ("lines", "growth", "outlook"):
        for l in getattr(p.history, k):
            yield f"history.{l.metric}.{l.value.period}", l.metric, l.value
    for k in p.kpis:
        yield f"kpis.{k.metric}.{k.value.period}", k.metric, k.value
    for i in p.addbacks.items:
        yield f"addbacks.items.{_key(i.name)}.amount", None, i.amount


def flatten(p: DealProfile, log: list[dict] | None = None) -> dict:
    """Field id -> value. Scalars: str, int, ISO date or Num. Lists (scored by keyword in the evaluator):
    business_model.revenue_streams, business_model.sales_channels, customers.key_partners, gaps (names), and
    addbacks.items ([(name, Num)]). A repeated row id keeps the first value; a different value is logged."""
    out: dict = {}

    def put(k, v):
        if v is None:
            return
        if k in out:
            if _dump(out[k]) != _dump(v) and log is not None:
                log.append({"field": k, "event": "duplicate_id", "kept": _dump(out[k]), "dropped": _dump(v)})
            return
        out[k] = v

    for area in ("overview", "business_model", "customers", "process"):
        for name, v in getattr(p, area):
            if isinstance(v, list):
                continue
            put(f"{area}.{name}", v.isoformat() if isinstance(v, date) else v)
    put("history.fiscal_year_end", p.history.fiscal_year_end)
    for f, _, v in iter_nums(p):
        if not f.startswith("addbacks.items."):
            put(f, v)
    for m in p.management:
        put(f"management.{_key(m.name)}.title", m.title)
        put(f"management.{_key(m.name)}.joined_year", m.joined_year)
    out["business_model.revenue_streams"] = [s.name for s in p.business_model.revenue_streams]
    out["business_model.sales_channels"] = list(p.business_model.sales_channels)
    out["customers.key_partners"] = list(p.customers.key_partners)
    out["addbacks.items"] = [(i.name, i.amount) for i in p.addbacks.items]
    out["gaps"] = list(p.disclosure_gaps)
    return out
