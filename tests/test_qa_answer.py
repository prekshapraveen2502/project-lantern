"""
Part 4 item 2 tests: deterministic evidence helpers (src/qa/evidence.py) and the answer pipeline (src/qa/answer.py)
with a scripted fake LLM and fixed evidence; no model, no index, no network.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import src.qa.evidence as evidence  # noqa: E402
from src.qa import answer as qa  # noqa: E402
from src.calc import core as calc  # noqa: E402

PARAMS = {"qa": {"max_regenerations": 1, "numeric_tolerance_rel": 0.005}, "llm": {"models": {"large": "m"}}}

# ---- evidence helpers ----

def test_metric_phrases_longest_match_wins_and_possessives_are_ignored():
    vocab = {"metrics": {"revenue": {"synonyms": ["net revenue"]}, "revenue_growth": {"synonyms": ["net revenue growth"]},
                         "top1_customer_share": {"synonyms": ["top partner share"]}}}
    ph = evidence.metric_phrases(vocab)
    assert evidence.match_metrics("How much did net revenue growth reach?", ph) == ["revenue_growth"]
    assert evidence.match_metrics("What is the top partner's share?", ph) == ["top1_customer_share"]


def test_question_years():
    assert evidence.question_years("revenue in FY2025 vs fiscal 2024 and FY23") == [2023, 2024, 2025]


def test_concept_score_follows_the_approved_rule():
    q = set(evidence.norm("What was Shift4's net cash used in investing activities in 2025?").split())
    assert evidence.concept_words("us-gaap:NetCashProvidedByUsedInInvestingActivities") == \
        ["net", "cash", "provided", "investing", "activities"]
    inv = evidence.concept_score("us-gaap:NetCashProvidedByUsedInInvestingActivities", q)
    assert inv == (4, 0.8) and inv > evidence.concept_score("us-gaap:Cash", q) == (1, 1.0)   # more words wins (b)
    assert evidence.concept_score("us-gaap:CashAndCashEquivalentsPeriodIncreaseDecrease", q)[1] < 0.75


def test_entity_aliases_from_companyfacts(tmp_path):
    for t, name, cik in [("FOUR", "SHIFT4 PAYMENTS, INC.", 1794669), ("GPN", "GLOBAL PAYMENTS INC.", 1123360)]:
        (tmp_path / t).mkdir()
        (tmp_path / t / "companyfacts.json").write_text(json.dumps({"entityName": name, "cik": cik}))
    al = evidence.entity_aliases(tmp_path, ["kestrel"])
    assert evidence.match_entities("Shift4's cash flow", al) == ["0001794669"]
    assert evidence.match_entities("Global Payments revenue", al) == ["0001123360"]
    assert evidence.match_entities("Kestrel revenue", al) == ["kestrel"]


def test_calcs_group_by_concept_when_there_is_no_metric():
    f = lambda fid, concept, y, v: {"fact_id": fid, "entity_id": "E", "metric": None, "concept": concept,
                                    "unit": "USD", "fiscal_year": y, "value": v, "tier": 1}
    facts = [f("a", "us-gaap:X", 2025, 120.0), f("b", "us-gaap:X", 2024, 100.0), f("c", "us-gaap:Y", 2024, 50.0)]
    got = evidence.calcs_for(facts, [])
    assert sorted(c["op"] for c in got) == ["difference", "growth"]
    assert all([t["fact_id"] for t in c["trace"]] == ["a", "b"] for c in got)


# ---- answer pipeline ----

FACT = {"fact_id": "F-rev25", "tier": 3, "entity_id": "kestrel", "metric": "revenue", "value": 59.3e6, "unit": "USD",
        "fiscal_year": 2025, "fiscal_period": "FY", "doc_id": "DR-kestrel-005",
        "citation": {"kind": "sheet_cell", "block_id": "s001", "cell": "P&L!D6"}}
FACT24 = {**FACT, "fact_id": "F-rev24", "fiscal_year": 2024, "value": 48.7e6}
CLAIM = {"claim_id": "C-gross", "tier": 4, "doc_id": "DR-kestrel-001", "block_id": "p0003_b002", "page": 3,
         "bbox": [76.4, 101.13, 528.37, 125.19], "metric": "revenue", "value": 96.4e6, "unit": "USD",
         "claim_text": "FY2025 revenue of $96.4m"}
GROWTH = calc.growth(FACT, FACT24)
EV = {"facts": [FACT, FACT24], "calcs": [GROWTH], "claims": [CLAIM],
      "chunks": [{"chunk_id": "x", "doc_id": "DR-kestrel-001", "block_ids": ["p0003_b002"], "tier": 4, "page": 3,
                  "text": "Kestrel reported FY2025 revenue of $96.4m"}]}
RECORDS = {("DR-kestrel-001", "p0003_b002"): {"page": 3, "bbox": [76.4, 101.13, 528.37, 125.19], "text": "..."}}


class FakeLLM:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def structured(self, response_model, messages, **kw):
        self.calls.append(messages)
        return self.answers.pop(0)


def claim(text, label, *numbers, cites=()):
    return qa.ModelClaim(text=text, label=label, citations=list(cites),
                         numbers=[qa.ModelNumber(value_text=v, ref_type=t, ref=r) for v, t, r in numbers])


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence, "gather", lambda q, s, ctx: EV)
    reg = tmp_path / "calcs.jsonl"

    def go(*answers):
        llm = FakeLLM(*answers)
        out = qa.answer_question("q", "kestrel", SimpleNamespace(params=PARAMS), llm=llm, records=RECORDS, registry=reg)
        return out, llm, reg
    return go


def test_citations_and_tiers_come_from_the_store_and_calcs_are_registered(run):
    ok = qa.ModelAnswer(abstain=False, claims=[
        claim("Net revenue was $59.3 million in FY2025.", "fact", ("$59.3 million", "fact_id", "F-rev25")),
        claim("It grew 21.8% in FY2025.", "fact", ("21.8%", "calc_id", GROWTH["calc_id"])),
        claim("The CIM states revenue of $96.4 million.", "management_claim", ("$96.4 million", "claim_id", "C-gross"),
              cites=["DR-kestrel-001:p0003_b002", "DR-kestrel-001:not-retrieved"])])
    out, llm, reg = run(ok)
    assert out.status == "answered" and len(llm.calls) == 1
    c0, c1, c2 = out.claims
    assert (c0.tier, c0.citations[0].block_id, c0.citations[0].page) == (3, "s001", None)
    assert [c.block_id for c in c1.citations] == ["s001"] and c1.tier == 3          # both inputs cite sheet s001
    assert c2.tier == 4 and c2.citations[0].bbox == [76.4, 101.13, 528.37, 125.19] and len(c2.citations) == 1
    assert list(calc.load(reg)) == [GROWTH["calc_id"]]


def test_a_claim_id_in_a_fact_claim_is_regenerated_with_feedback(run):
    bad = qa.ModelAnswer(abstain=False, claims=[claim("Revenue was $96.4 million.", "fact",
                                                      ("$96.4 million", "claim_id", "C-gross"))])
    fixed = qa.ModelAnswer(abstain=False, claims=[claim("Management says revenue was $96.4 million.",
                                                        "management_claim", ("$96.4 million", "claim_id", "C-gross"))])
    out, llm, _ = run(bad, fixed)
    assert out.status == "answered" and len(llm.calls) == 2
    assert "must be labelled management_claim" in llm.calls[1][1]["content"]


def test_a_claim_still_failing_after_one_regeneration_is_dropped(run):
    good = claim("Net revenue was $59.3 million.", "fact", ("$59.3 million", "fact_id", "F-rev25"))
    bad = claim("Margin was 40%.", "fact", ("40%", "fact_id", "F-unknown"))
    out, llm, _ = run(qa.ModelAnswer(abstain=False, claims=[good, bad]), qa.ModelAnswer(abstain=False, claims=[good, bad]))
    assert out.status == "partially_abstained" and [c.text for c in out.claims] == [good.text]
    assert len(llm.calls) == 2 and "1 claim(s) dropped" in out.reason


def test_every_claim_failing_means_not_disclosed(run):
    bad = qa.ModelAnswer(abstain=False, claims=[claim("Margin was 40%.", "fact", ("40%", "fact_id", "F-unknown"))])
    out, _, _ = run(bad, bad)
    assert out.status == "not_disclosed" and out.rendered.startswith("Not disclosed.") and not out.claims


def test_abstention_carries_the_reason(run):
    out, llm, _ = run(qa.ModelAnswer(abstain=True, reason="Churn by cohort is not disclosed in the CIM."))
    assert out.status == "not_disclosed" and len(llm.calls) == 1
    assert out.rendered == "Not disclosed. Churn by cohort is not disclosed in the CIM."


def test_prompt_asks_for_minus_signs_and_lists_every_evidence_id():
    msgs = qa.messages("q", EV)
    assert "minus sign, never brackets" in msgs[0]["content"]
    for i in ("F-rev25", "F-rev24", GROWTH["calc_id"], "C-gross", "DR-kestrel-001:p0003_b002"):
        assert i in msgs[1]["content"]


def test_a_number_that_does_not_match_its_reference_is_regenerated_with_the_linter_feedback(run):
    wrong = qa.ModelAnswer(abstain=False, claims=[claim("Net revenue was $59.0 million.", "fact",
                                                        ("$59.0 million", "fact_id", "F-rev25"))])
    right = qa.ModelAnswer(abstain=False, claims=[claim("Net revenue was $59.3 million.", "fact",
                                                        ("$59.3 million", "fact_id", "F-rev25"))])
    out, llm, _ = run(wrong, right)
    assert out.status == "answered" and len(llm.calls) == 2
    assert '"$59.0 million" (read as 59000000.0) matches no value' in llm.calls[1][1]["content"]


def test_tier_3_4_5_content_is_labelled_in_the_prose():
    cite = lambda doc, tier: qa.Citation(doc_id=doc, page=None, bbox=None, block_id="b", tier=tier)
    c = lambda text, tier, doc, label="fact": qa.Claim(text=text, label=label, tier=tier, numbers=[],
                                                       citations=[cite(doc, tier)])
    forms = {"DR-kestrel-001": "cim", "DR-kestrel-006": "deck", "DR-kestrel-008": "transcript"}
    got = qa.render([c("Filed revenue was X.", 1, "0001"), c("Net revenue was X.", 3, "DR-kestrel-005"),
                     c("The CIM says X.", 4, "DR-kestrel-001", "management_claim"),
                     c("The deck shows X", 4, "DR-kestrel-006", "management_claim"),
                     c("The CFO said X.", 4, "DR-kestrel-008", "management_claim"),
                     c("The website says X.", 5, "WEB-1", "public_web")], forms)
    assert got == ("Filed revenue was X. Net revenue was X (per seller financials). The CIM says X (per CIM, unverified). "
                   "The deck shows X (per management deck, unverified). The CFO said X (per management call, unverified). "
                   "The website says X (per public website, unverified).")


def test_an_abstention_reason_with_a_number_is_regenerated_then_replaced(run):
    numbered = qa.ModelAnswer(abstain=True, reason="Only a CY2025 expectation of $64.2m is given.")
    out, llm, _ = run(numbered, numbered)
    assert len(llm.calls) == 2 and "Abstention reason:" in llm.calls[1][1]["content"]
    assert out.rendered == "Not disclosed. The evidence does not answer the question."
    clean = qa.ModelAnswer(abstain=True, reason="The data room has no FY2026 actuals.")
    out, llm, _ = run(numbered, clean)
    assert out.rendered == "Not disclosed. The data room has no FY2026 actuals."
