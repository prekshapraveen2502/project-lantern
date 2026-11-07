"""
Part 4 item 3 tests: the numeric-claim linter (src/qa/lint.py).

Precision is measured on tests/fixtures/lint/labelled_numbers.jsonl (34 snippets from real indexed blocks plus written
cases, 108 labelled spans: 59 numbers, 49 exclusions of years, dates, item numbers, form names and period labels),
labelled and reviewed before the linter first ran. A linter span counts as a true positive when it overlaps a span
labelled number, a false positive when it overlaps a span labelled exclude or no labelled span.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.qa import lint  # noqa: E402

LABELS = [json.loads(l) for l in open(ROOT / "tests" / "fixtures" / "lint" / "labelled_numbers.jsonl", encoding="utf-8")]


def test_labelled_set_size():
    spans = [t for r in LABELS for t in r["tokens"]]
    assert len(spans) >= 50 and sum(t["label"] == "exclude" for t in spans) >= 20
    assert {t["type"] for t in spans if t["label"] == "exclude"} >= {"year", "date", "item_number", "form", "period"}


def test_linter_precision_on_the_labelled_numbers(capsys):
    r = lint.score_labelled(ROOT / "tests" / "fixtures" / "lint" / "labelled_numbers.jsonl")
    with capsys.disabled():
        print(f"\n[lint] labelled spans {r['labelled_spans']}; linter numbers {r['linter_numbers']}: "
              f"TP {r['true_positives']}, FP {r['false_positives']}, precision {r['precision']}; "
              f"missed {len(r['missed_numbers'])}; wrong values {len(r['wrong_values'])}")
    assert r["labelled_spans"] == 108
    assert not r["false_positive_spans"] and not r["missed_numbers"] and not r["wrong_values"]


@pytest.mark.parametrize("text,value", [
    ("$4.9bn", 4.9e9), ("$350mm", 350e6), ("12.5×", 12.5), ("9.8x", 9.8), ("16.8%", 0.168), ("-5.2%", -0.052),
    ("$(12.4) million", -12.4e6), ("(8.1)%", -0.081), ("(10,131)", -10131), ("| Cash used | (976) |", -976),
    ("($4.6m)", 4.6e6), ("five (5) years", 5), ("NZ$325 million", 325e6), ("2,050 merchants", 2050)])
def test_number_forms(text, value):
    got = lint.numbers(text)
    assert len(got) == 1 and lint.close(got[0].value, value, 1e-9), got


@pytest.mark.parametrize("text,typ", [
    ("in 2024", "year"), ("2025-26", "year"), ("June 30, 2026", "date"), ("03/15/2025", "date"),
    ("00:01:47", "date"), ("Item 1A", "item_number"), ("(2) Excludes", "item_number"), ("Form 10-K", "form"),
    ("8-K", "form"), ("Q2", "period"), ("FY24", "period"), ("FY2025", "period"), ("fiscal 2026", "period")])
def test_exclusions(text, typ):
    spans = lint.parse(text)
    assert not lint.numbers(text) and any(s.type == typ for s in spans), spans


def test_digits_inside_words_are_not_numbers():
    assert lint.numbers("Shift4's block p0003_b002 uses 4G") == []


def test_lint_claim_accepts_matches_within_tolerance_and_explains_failures():
    refs = {"F-rev": 59_300_000.0, "CALC-g": 0.217659}
    assert lint.lint_claim("Net revenue was $59.3 million in FY2025, up 21.8%.", refs, 0.005) == []
    bad = lint.lint_claim("Net revenue was $59.0 million, up 22%.", refs, 0.005)
    assert len(bad) == 2 and '"$59.0 million"' in bad[0] and "F-rev = 59300000.0" in bad[0]
    assert lint.lint_claim("Revenue was 59.3.", refs, 0.005)            # no scale -> 59.3, no match
    assert lint.lint_claim("Kestrel has 410 employees.", {}, 0.005)     # a number with no reference


def test_minus_before_the_currency_sign():
    got = lint.numbers("Net cash used in investing activities was -$194.2 million in 2025.")
    assert len(got) == 1 and got[0].value == -194.2e6 and got[0].text == "-$194.2 million"
