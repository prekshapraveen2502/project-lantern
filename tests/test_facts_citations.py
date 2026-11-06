"""
Part 1 item 3 tests: XBRL citations (ixbrl_element, the ixbrl_context fallback) and the citation-text check
value = (-1 if sign == "-" else 1) x text x 10^scale. Cases use the facts checked online on 2026-11-05.
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts.build import citation_for, fact_id, fact_id_for, text_check  # noqa: E402
from src.facts.xbrl_read import citation_value  # noqa: E402

ACCN, CIK, DOC = "0001628280-26-058238", "0001786352", "bill-20260630.htm"
SRC = "data/edgar_comps/BILL/10-K/0001628280-26-058238/unpacked/bill-20260630.htm"


def tag(element_id="f-148", text="1,653,160", sign=None, scale=3, value=1653160000.0, also=("f-577",)):
    return {"concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "context_ref": "c-1",
            "unit_ref": "usd", "element_id": element_id, "also_tagged": list(also), "text": text, "sign": sign,
            "scale": scale, "value": value}


def test_ixbrl_element_citation_has_path_url_with_anchor_text_and_sign():
    c = citation_for(tag(), ACCN, CIK, DOC, SRC)
    assert c["kind"] == "ixbrl_element" and c["element_id"] == "f-148"
    assert c["url"] == "https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-148"
    assert (c["source_path"], c["text"], c["sign"], c["also_tagged"]) == (SRC, "1,653,160", None, ["f-577"])
    assert fact_id_for(ACCN, tag()) == fact_id(ACCN, "f-148")


def test_a_tag_without_an_id_falls_back_to_ixbrl_context():
    t = tag(element_id=None, also=("f-9", None))
    c = citation_for(t, ACCN, CIK, DOC, SRC)
    assert c["kind"] == "ixbrl_context" and "element_id" not in c
    assert (c["concept"], c["context_ref"], c["unit_ref"]) == (t["concept"], "c-1", "usd")
    assert c["url"].endswith("/bill-20260630.htm") and "#" not in c["url"]
    assert c["also_tagged"] == ["f-9"]
    key = f"{ACCN}|{t['concept']}|c-1|usd"
    assert fact_id_for(ACCN, t) == "F-" + hashlib.sha256(key.encode()).hexdigest()[:12]
    assert fact_id_for(ACCN, t) == fact_id_for(ACCN, dict(t))          # stable
    assert fact_id_for(ACCN, t) != fact_id_for(ACCN, tag())


def test_citation_value_applies_sign_and_scale():
    assert citation_value("1,653,160", 3, None) == 1653160000.0      # BILL 10-K #f-148
    assert citation_value("8,682", 6, None) == 8682000000.0          # PYPL 10-Q #f-106
    assert citation_value("1,234", 3, "-") == -1234000.0              # shown without a minus sign, sign="-"
    assert citation_value("0.5", 0, None) == 0.5
    assert citation_value("—", 3, None) is None                       # a dash for zero: check does not apply
    assert citation_value(".6667", 0, None) == 0.6667                 # no leading digit
    assert citation_value("5.", 0, None) == 5.0


def test_nested_tag_text_comes_from_the_inner_tag():
    """Local only: BILL's f-114 (PreferredStockSharesAuthorized, scale 3) wraps another tag holding "10,000"."""
    import json
    import pytest
    import yaml
    from src.facts.xbrl_read import read_filing
    fdirs = list((ROOT / "data" / "edgar_comps" / "BILL" / "10-K").glob("*/unpacked"))
    if not fdirs:
        pytest.skip("BILL 10-K not present (DVC data)")
    fdir = fdirs[0].parent
    doc = json.loads((fdir / "filing.json").read_text())["doc"]
    cache = ROOT / yaml.safe_load((ROOT / "params.yaml").read_text())["facts"]["arelle_cache"]
    t = next(t for t in read_filing(fdir / "unpacked" / doc, cache)["tags"] if t["element_id"] == "f-114")
    assert t["text"] == "10,000" and citation_value(t["text"], t["scale"], t["sign"]) == t["value"] == 10000000.0


def test_decimal_text_resolves_within_relative_tolerance():
    """"1.2" x 10^9 is computed in floating point; the check compares with a relative tolerance, not ==."""
    assert citation_value("1.2", 9, None) == 1.2 * 10 ** 9
    out = text_check([tag(element_id="f-5", text="1.2", scale=9, value=1200000000.0),
                      tag(element_id="f-6", text="0.3", scale=9, value=0.1e9 + 0.2e9),          # 300000000.00000006
                      tag(element_id="f-7", text="1.2", scale=9, value=1200001000.0)])          # off by 1,000: not equal
    assert (out["resolves"], out["mismatched"]) == (2, 1) and out["mismatches"][0]["element_id"] == "f-7"


def test_text_check_counts_negative_values_as_resolving():
    facts = [tag(), tag(element_id="f-2", text="1,234", sign="-", value=-1234000.0),
             tag(element_id="f-3", text="1,234", sign=None, value=-1234000.0),       # sign missing: mismatch
             tag(element_id="f-4", text="—", value=0.0)]
    out = text_check(facts)
    assert (out["resolves"], out["mismatched"], out["not_numeric"]) == (2, 1, 1)
    assert out["mismatches"][0]["element_id"] == "f-3"
