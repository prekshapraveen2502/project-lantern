"""Part 5 item 3: memo checks and rendering (no LLM, no index)."""
import yaml

from src.extract import memo as M
from src.qa.answer import Citation, Claim, ModelClaim, ModelNumber, NumberRef, Resolver

EV = {"facts": [{"fact_id": "F-aaaaaaaaaaaa", "value": 0.168, "tier": 3, "doc_id": "DR-k-005", "unit": "pure",
                 "citation": {"block_id": "s004"}}],
      "calcs": [], "claims": [{"claim_id": "C-bbbbbbbbbbbb", "value": 0.10, "tier": 4, "doc_id": "DR-k-001",
                               "block_id": "p0006_b002", "page": 6, "bbox": None}],
      "chunks": [{"doc_id": "DR-k-001", "block_ids": ["p0011_b001"], "tier": 4, "page": 11}]}


def test_template_has_the_brief_headings_and_no_numbers_in_instructions():
    t = yaml.safe_load(open("config/memo_template.yaml", encoding="utf-8"))
    assert [s["heading"] for s in t["sections"]] == [
        "Company Overview", "Business Model and Go-to-Market", "Financial Profile", "Customers and Concentration",
        "Management and Organization", "Process and Valuation Context", "Key Risks and Diligence Questions",
        "Disclosure Gaps"]
    assert all(not M.lint_numbers(s["instruction"]) for s in t["sections"])


def test_clean_moves_non_numbers_to_citations():
    out = M.MemoSection(claims=[ModelClaim(text="Dana Whitfield is the CEO.", label="management_claim", numbers=[
        ModelNumber(value_text="Chief Executive Officer", ref_type="fact_id", ref="DR-k-001:p0010_b001"),
        ModelNumber(value_text="October 30, 2026", ref_type="fact_id", ref="F-x"),
        ModelNumber(value_text="16.8%", ref_type="fact_id", ref="F-aaaaaaaaaaaa")])])
    assert M.clean(out) == 2
    mc = out.claims[0]
    assert [n.value_text for n in mc.numbers] == ["16.8%"] and mc.citations == ["DR-k-001:p0010_b001"]


def test_section_problems_lint_claims_and_gaps():
    res = Resolver(EV, {})
    ok = ModelClaim(text="The top partner was 16.8% of net revenue.", label="fact",
                    numbers=[ModelNumber(value_text="16.8%", ref_type="fact_id", ref="F-aaaaaaaaaaaa")])
    bad_num = ModelClaim(text="The top 10 customers were 16.8% of net revenue.", label="fact",
                         numbers=[ModelNumber(value_text="16.8%", ref_type="fact_id", ref="F-aaaaaaaaaaaa")])
    bad_label = ModelClaim(text="No partner exceeds 10%.", label="fact",
                           numbers=[ModelNumber(value_text="10%", ref_type="claim_id", ref="C-bbbbbbbbbbbb")])
    out = M.MemoSection(claims=[ok, bad_num, bad_label], gaps=["churn by cohort", "the 3 largest contracts"])
    bad = M.section_problems(out, res, 0.005)
    assert sorted(bad) == [1, 2, 1001]


def claim(text, numbers=(), cites=(), tier=4, label="management_claim"):
    return Claim(text=text, label=label, tier=tier, numbers=[NumberRef(**n) for n in numbers],
                 citations=[Citation(doc_id=d, page=None, bbox=None, block_id=b, tier=tier) for d, b in cites])


def test_render_line_and_gap():
    forms = {"DR-k-001": "cim"}
    c = claim("Management claims no partner exceeds 10% of net revenue.",
              [{"value_text": "10%", "ref_type": "claim_id", "ref": "C-bbbbbbbbbbbb"}], [("DR-k-001", "p0006_b002")])
    assert M.render_line(c, forms, False) == ("Management claims no partner exceeds 10% of net revenue (per CIM, "
                                              "unverified). [C-bbbbbbbbbbbb; DR-k-001:p0006_b002]")
    g = claim("The terms of the sponsor-bank agreement are not disclosed.", cites=[("DR-k-001", "p0011_b001")])
    assert M.render_gap(g, forms) == ("- Terms of the sponsor-bank agreement: Not disclosed. (per CIM, unverified) "
                                      "[DR-k-001:p0011_b001]")
    assert M.render_gap(c, forms) is None
