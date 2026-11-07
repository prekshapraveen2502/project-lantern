"""Part 4 item 2: deterministic evidence for one question (decisions Q5 a and calc a). No LLM is used here.

  chunks  the top retrieval.k chunks from the pinned collection (retrieval.strategy/chunk_tokens/overlap_tokens/
          embed), hybrid, scope filter in the store (src/retrieve.py rank)
  facts   (1) facts whose citation block is one of the retrieved blocks (data-room cells), and (2) a lookup: question
          words matched to config/metrics_vocab.yaml metric names and synonyms, entities named in the question
          (ticker, company name; else the scope's own deal), fiscal years mentioned (plus the year before, for growth);
          undimensioned FY facts only, in scope
  claims  tier-4/5 claims cited in a retrieved block, or matching a looked-up metric and entity, in scope
  calcs   precomputed from the looked-up facts (src/calc/core.py): growth and difference for each consecutive pair
          of fiscal years of the same entity, metric and unit; ratio for the derived metrics whose formula the vocab
          states (take_rate = revenue / tpv, subscription_share = subscription_revenue / revenue). The model only
          references them; it never computes a number.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
from pathlib import Path

import yaml

from src.calc import core as calc
from src.qa import store
from src.retrieve import rank, scope_filter

DERIVED_INPUTS = {"revenue_growth": [("growth", "revenue")],
                  "take_rate": [("ratio", "revenue", "tpv")],
                  "subscription_share": [("ratio", "subscription_revenue", "revenue")]}
MAX_FACTS = 40
DEALS = ("deal_a", "deal_b", "kestrel")
CONCEPT_STOP = {"by", "used", "in", "of", "and", "for", "from", "the", "to"}
CONCEPT_MIN = 0.75
NAME_SUFFIXES = {"inc", "corporation", "corp", "holdings", "co", "ltd", "plc", "the"}


def norm(text: str) -> str:
    """Lower case, drop possessive 's, every non-alphanumeric run (except &) to one space."""
    t = re.sub(r"['’]s\b", "", (text or "").lower())
    return " " + re.sub(r"[^a-z0-9&]+", " ", t).strip() + " "


def metric_phrases(vocab: dict) -> list[tuple[str, str]]:
    out = []
    for m, d in vocab["metrics"].items():
        for p in [m.replace("_", " ")] + list(d.get("synonyms") or []):
            out.append((norm(p), m))
    return sorted(out, key=lambda x: -len(x[0]))                  # longest phrase first


def match_metrics(question: str, phrases) -> list[str]:
    q, found = norm(question), []
    for p, m in phrases:
        if p in q:
            q = q.replace(p, " ")                                  # a phrase is used once (longest wins)
            found.append(m)
    return list(dict.fromkeys(found))


def entity_aliases(comps_dir: Path, deals: list[str]) -> dict[str, str]:
    """alias (normalised) -> entity_id: each comp's ticker, the first word of its EDGAR name (when not generic) and
    the name without corporate suffixes; each deal's id."""
    out = {}
    for d in sorted(Path(comps_dir).glob("*/companyfacts.json")):
        ticker = d.parent.name
        cf = json.loads(d.read_text(encoding="utf-8"))
        cik = str(cf.get("cik", "")).zfill(10)
        words = [w for w in norm(cf.get("entityName", "")).split() if w not in NAME_SUFFIXES]
        for a in {ticker.lower(), " ".join(words), words[0] if words and words[0] not in {"global"} else ""}:
            if a:
                out[norm(a)] = cik
    for deal in deals:
        out[norm(deal.replace("_", " "))] = deal
    return out


def match_entities(question: str, aliases: dict[str, str]) -> list[str]:
    q = norm(question)
    return list(dict.fromkeys(e for a, e in sorted(aliases.items(), key=lambda x: -len(x[0])) if a in q))


def concept_words(concept: str) -> list[str]:
    """us-gaap:NetCashProvidedByUsedInOperatingActivities -> [net, cash, provided, operating, activities]."""
    local = concept.split(":", 1)[-1]
    return [w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", local) if w.lower() not in CONCEPT_STOP]


def concept_score(concept: str, qwords: set[str]) -> tuple[int, float]:
    """(matched words, matched / concept words): the concept matching the most question words wins, then the higher
    share; a concept whose share is below CONCEPT_MIN is never used (decision 2026-11-06, Q1 a revised to b: the
    one-word concept Cash must not beat a cash-flow line). Tuned on the dev suite; the variant suite is held out."""
    w = concept_words(concept)
    hit = sum(x in qwords for x in w)
    return (hit, hit / len(w)) if w else (0, 0.0)


def question_years(question: str) -> list[int]:
    years = set()
    for m in re.finditer(r"\b(?:FY|fiscal\s+|CY)?(20\d\d)\b", question, re.I):
        years.add(int(m.group(1)))
    for m in re.finditer(r"\bFY(\d\d)\b", question, re.I):
        years.add(2000 + int(m.group(1)))
    return sorted(years)


def doc_forms(dataroom_dir: Path) -> dict[str, str]:
    """doc_id -> form (cim, deck, transcript, financials, ...) from each deal's export manifest."""
    out = {}
    for f in sorted(Path(dataroom_dir).glob("*/export/manifest.csv")):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            out[r["accession"]] = r["form"]
    return out


class Context:
    """Everything evidence needs, loaded once (the service keeps one). Chroma rewrites chroma.sqlite3 when a client
    opens it (DECISIONS 2026-11-06), so the index is copied to a temporary directory and queried there."""

    def __init__(self, params: dict, *, facts_db: Path, index_dir: Path, comps_dir: Path, dataroom_dir: Path,
                 vocab_path: Path, embed_query_for=None):
        from src.retrieve import open_collection
        self.params, self.facts_db = params, Path(facts_db)
        r = params["retrieval"]
        self.pinned = dict(strategy=r["strategy"], size=int(r["chunk_tokens"]), overlap=int(r["overlap_tokens"]),
                           which=r["embed"])
        self._tmp = tempfile.TemporaryDirectory(prefix="lantern-qa-")
        shutil.copytree(Path(index_dir) / "chroma", Path(self._tmp.name) / "chroma")
        self.collection = open_collection(Path(self._tmp.name), **self.pinned)
        self.phrases = metric_phrases(yaml.safe_load(Path(vocab_path).read_text(encoding="utf-8")))
        deals = sorted(p.parent.parent.name for p in Path(dataroom_dir).glob("*/export/manifest.csv"))
        self.aliases = entity_aliases(comps_dir, deals)
        self.forms = doc_forms(dataroom_dir)
        self.embed_query = embed_query_for(self.pinned["which"]) if embed_query_for else None


def _allowed(scope: str) -> list[str]:
    return scope_filter(scope)["deal_id"]["$in"]


def gather(question: str, scope: str, ctx: Context) -> dict:
    allowed = _allowed(scope)                                      # raises ScopeError first
    k = int(ctx.params["retrieval"]["k"])
    ranked = rank(question, scope, collection=ctx.collection, which=ctx.pinned["which"], params=ctx.params,
                  depth=k, embed_query=ctx.embed_query)
    ids = [cid for cid, _ in ranked["hybrid"]]
    docs = dict(zip(*[ctx.collection.get(ids=ids, include=["documents"])[x] for x in ("ids", "documents")])) \
        if ids else {}
    chunks = []
    for cid in ids:
        m = ranked["meta"][cid]
        chunks.append({"chunk_id": cid, "doc_id": m["doc_id"], "deal_id": m["deal_id"], "tier": m["tier"],
                       "page": None if m["page"] == -1 else m["page"], "block_ids": json.loads(m["block_ids"]),
                       "text": docs.get(cid, "")})
    blocks = list(dict.fromkeys((c["doc_id"], b) for c in chunks for b in c["block_ids"]))   # retrieval rank order

    metrics = match_metrics(question, ctx.phrases)
    lookup = list(dict.fromkeys(metrics + [i for m in metrics for op in DERIVED_INPUTS.get(m, []) for i in op[1:]]))
    ents = match_entities(question, ctx.aliases)
    ents = [e for e in ents if e not in DEALS or e in allowed] or ([scope] if scope != "public" else [])
    years = question_years(question)
    con = store.connect(ctx.facts_db)
    try:
        facts, seen = [], set()

        def add_facts(sql, args):
            cur = con.execute(sql, args)
            for row in cur.fetchall():
                f = store._row(cur, row)
                if f["fact_id"] not in seen and len(facts) < MAX_FACTS:
                    seen.add(f["fact_id"]); facts.append(f)

        q_in = lambda xs: ",".join("?" * len(xs))
        base = (f"select * from facts where deal_id in ({q_in(allowed)}) and fiscal_period in ('FY', 'instant') "
                "and (dims is null or dims::varchar in ('{}', '[]', 'null'))")
        looked = []
        if lookup and ents:
            sql = base + f" and metric in ({q_in(lookup)}) and entity_id in ({q_in(ents)})"
            args = allowed + lookup + ents
            if years:
                yrs = sorted({y for x in years for y in (x, x - 1)})
                sql += f" and fiscal_year in ({q_in(yrs)})"
                args += yrs
            add_facts(sql + " order by entity_id, metric, fiscal_year desc, \"end\" desc, fact_id", args)
            looked = list(facts)
        concepts = []
        pub = [e for e in ents if e not in DEALS]
        if pub:                                                    # decision 2026-11-06 Q1 (a): concept names
            sql = (base + f" and tier = 1 and concept is not null and entity_id in ({q_in(pub)})")
            args = allowed + pub
            if years:
                yrs = sorted({y for x in years for y in (x, x - 1)})
                sql += f" and fiscal_year in ({q_in(yrs)})"
                args += yrs
            cur = con.execute(sql + " order by entity_id, concept, fiscal_year desc, \"end\" desc, fact_id", args)
            rows = [store._row(cur, r) for r in cur.fetchall()]
            qwords = set(norm(question).split())
            scored = {c: concept_score(c, qwords) for c in {r["concept"] for r in rows}}
            best = max((s for s in scored.values() if s[1] >= CONCEPT_MIN), default=None)
            concepts = sorted(c for c, s in scored.items() if s == best)
            for f in rows:
                if f["concept"] in concepts and f["fact_id"] not in seen and len(facts) < MAX_FACTS:
                    seen.add(f["fact_id"]); facts.append(f); looked.append(f)
        for doc, b in blocks:
            add_facts(base + " and doc_id = ? and json_extract_string(citation, '$.block_id') = ? order by fact_id",
                      allowed + [doc, b])
        cur = con.execute(f"select * from claims where deal_id in ({q_in(allowed)}) order by claim_id", allowed)
        claims = [c for c in (store._row(cur, r) for r in cur.fetchall())
                  if (c["doc_id"], c["block_id"]) in set(blocks) or (c["metric"] in lookup and c["entity_id"] in ents)]
    finally:
        con.close()
    return {"question": question, "scope": scope, "metrics": metrics, "entities": ents, "years": years,
            "chunks": chunks, "facts": facts, "claims": claims, "calcs": calcs_for(looked, metrics)}


def calcs_for(facts: list[dict], metrics: list[str]) -> list[dict]:
    out = []
    by = {}
    for f in facts:
        line = f["metric"] or f.get("concept")                    # concept-matched facts have no vocab metric
        by.setdefault((f["entity_id"], line, f["unit"]), {}).setdefault(f["fiscal_year"], f)
    for (ent, met, unit), yrs in sorted(by.items()):
        for y in sorted(yrs):
            if y - 1 in yrs:
                for fn in (calc.growth, calc.difference):
                    try:
                        out.append(fn(yrs[y], yrs[y - 1]))
                    except calc.CalcError:
                        pass
    for m in metrics:
        for op in DERIVED_INPUTS.get(m, []):
            if op[0] != "ratio":
                continue
            num = {(f["entity_id"], f["fiscal_year"]): f for f in facts if f["metric"] == op[1]}
            den = {(f["entity_id"], f["fiscal_year"]): f for f in facts if f["metric"] == op[2]}
            for key in sorted(set(num) & set(den)):
                try:
                    out.append(calc.ratio(num[key], den[key]))
                except calc.CalcError:
                    pass
    return list({c["calc_id"]: c for c in out}.values())
