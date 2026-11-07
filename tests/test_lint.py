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


def labelled_spans(row):
    out, pos = [], 0
    for t in row["tokens"]:
        i = row["text"].find(t["span"], pos)
        out.append({**t, "start": i, "end": i + len(t["span"])})
        pos = i + len(t["span"])
    return out


def score():
    tp, fp, missed, wrong = [], [], [], []
    for row in LABELS:
        gold = labelled_spans(row)
        got = lint.numbers(row["text"])
        for s in got:
            over = [g for g in gold if g["start"] < s.end and s.start < g["end"]]
            if any(g["label"] == "number" for g in over):
                g = next(g for g in over if g["label"] == "number")
                tp.append((row["id"], s.text))
                if not lint.close(s.value, g["value"], 1e-9):
                    wrong.append((row["id"], s.text, s.value, g["value"]))
            else:
                fp.append((row["id"], s.text, [g.get("type") for g in over]))
        for g in gold:
            if g["label"] == "number" and not any(s.start < g["end"] and g["start"] < s.end for s in got):
                missed.append((row["id"], g["span"]))
    return tp, fp, missed, wrong


def test_labelled_set_size():
    spans = [t for r in LABELS for t in r["tokens"]]
    assert len(spans) >= 50 and sum(t["label"] == "exclude" for t in spans) >= 20
    assert {t["type"] for t in spans if t["label"] == "exclude"} >= {"year", "date", "item_number", "form", "period"}


def test_linter_precision_on_the_labelled_numbers(capsys):
    tp, fp, missed, wrong = score()
    precision = len(tp) / (len(tp) + len(fp))
    with capsys.disabled():
        print(f"\n[lint] labelled spans {sum(len(r['tokens']) for r in LABELS)}; linter numbers {len(tp) + len(fp)}: "
              f"TP {len(tp)}, FP {len(fp)}, precision {precision:.4f}; labelled numbers missed {len(missed)}; "
              f"wrong values {len(wrong)}")
        for x in fp + missed + wrong:
            print("   ", x)
    assert not fp and not missed and not wrong


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
