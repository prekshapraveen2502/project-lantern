"""Part 4 item 3: the numeric-claim linter.

parse(text) finds every number-like span and classifies it:
  number   with its normalised value: $ and other currency prefixes (NZ$, US$, €, £) dropped; scale words and
           suffixes thousand/k, million/m/mm, billion/bn/b, trillion; % divides by 100; × or x marks a multiple;
           a leading minus sign is negative; parentheses as negative (decision 2026-11-06):
             - inside a table row (a line with "|" cells) a parenthesised number is always negative;
             - in prose only when the brackets hold $, %, a decimal point or a thousands comma ($(12.4), (8.1)%,
               (10,131)); with a scale letter inside, "($4.6m)", the brackets are an aside and the value positive;
             - a bare small integer in brackets is an excluded list/footnote marker, unless its number word comes
               just before it ("five (5)") -> a positive number.
  exclude  with its type:
             year         a bare 4-digit integer 1990-2099 with no $, %, scale word or thousands comma; "2025-26"
             date         "June 30, 2026", "Jan 2025", "03/15/2025", times such as 00:01:47
             item_number  "Item 1A", list markers "1." at the start of a sentence, "(2)"
             form         10-K, 10-Q, 8-K, 20-F, S-1 (with /A)
             period       Q1-Q4, H1/H2, FY24, FY2025, CY2025, "fiscal 2026"
Digits inside a word (Shift4, p0003) are not numbers.

lint_claim(text, refs, tol) checks every number in a claim against the values the claim references (fact, calc or
claim values): relative gap <= qa.numeric_tolerance_rel; it returns the problems as feedback sentences.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SCALES = {"thousand": 1e3, "thousands": 1e3, "k": 1e3, "million": 1e6, "millions": 1e6, "m": 1e6, "mm": 1e6,
          "mn": 1e6, "billion": 1e9, "billions": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12, "tn": 1e12}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}
MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|" \
         r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"

# exclusions, tried before numbers; each match masks its characters
EXCLUDE = [
    ("date", re.compile(rf"\b{MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+(?:19|20)\d\d\b", re.I)),
    ("date", re.compile(rf"\b\d{{1,2}}\s+{MONTHS}\s+(?:19|20)\d\d\b", re.I)),
    ("date", re.compile(rf"\b{MONTHS}\s+(?:19|20)\d\d\b", re.I)),
    ("date", re.compile(r"\b\d{1,2}/\d{1,2}/(?:19|20)?\d\d\b")),
    ("date", re.compile(r"\b(?:19|20)\d\d-\d\d-\d\d\b")),
    ("date", re.compile(r"\b\d{1,2}:\d\d(?::\d\d)?\b")),
    ("form", re.compile(r"\b(?:10-K|10-Q|8-K|20-F|40-F|6-K|S-1|S-4|DEF 14A)(?:/A)?\b", re.I)),
    ("item_number", re.compile(r"\bItem\s+\d{1,2}[A-C]?\b", re.I)),
    ("period", re.compile(r"\b(?:FY|CY)\s?(?:19|20)?\d\d\b", re.I)),
    ("period", re.compile(r"\bfiscal\s+(?:year\s+)?(?:19|20)\d\d\b", re.I)),
    ("period", re.compile(r"\b[QH][1-4]\b")),
    ("year", re.compile(r"(?<![\d$,.])(?:19|20)\d\d[-–/]\d\d(?![\d%])")),
]
NUM = re.compile(
    r"(?P<neg>[-−–])?(?P<cur>(?:[A-Z]{1,3})?\$|€|£)?(?P<pneg>\()?(?P<cur2>\$)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?P<pclose>\))?(?P<pct>\s?%)?(?P<mult>\s?[×x](?![a-zA-Z]))?"
    r"(?P<suf>(?:mm|mn|bn|tn|m|b|k)(?![a-zA-Z]))?"
    r"(?P<scale>\s+(?:thousands?|millions?|billions?|trillion|mm|bn)\b)?")


@dataclass
class Span:
    start: int
    end: int
    text: str
    kind: str                     # number | exclude
    type: str | None = None       # exclusion type
    value: float | None = None


def _masked(spans, i, j):
    return any(s.start < j and i < s.end for s in spans)


def parse(text: str) -> list[Span]:
    spans: list[Span] = []
    for typ, rx in EXCLUDE:
        for m in rx.finditer(text):
            if not _masked(spans, m.start(), m.end()):
                spans.append(Span(m.start(), m.end(), m.group(0), "exclude", typ))
    # list markers "1." at a sentence start
    for m in re.finditer(r"(?:^|(?<=[.;:!?]\s)|(?<=\n))(\d{1,2})\.(?=\s+[A-Z(])", text):
        if not _masked(spans, m.start(1), m.end()):
            spans.append(Span(m.start(1), m.end(), m.group(0).strip(), "exclude", "item_number"))
    for m in NUM.finditer(text):
        i, j = m.start(), m.end()
        if m.group("scale") is None and m.group("suf") is None:
            j = m.end("pclose") if m.group("pclose") and not (m.group("pct") or m.group("mult")) else j
        if _masked(spans, m.start("num"), m.end("num")):
            continue
        if i > 0 and (text[i - 1].isalpha() or text[i - 1] in "_") and not m.group("cur"):
            continue                                               # digits inside a word: Shift4, p0003
        if m.end("num") < len(text) and text[m.end("num")].isalpha() and not (m.group("suf") or m.group("mult")):
            continue                                               # 4G, 2nd
        span = _classify(text, m, i, j)
        if span is not None:
            spans.append(span)
    return sorted(spans, key=lambda s: s.start)


def _classify(text: str, m, i: int, j: int) -> Span | None:
    raw = m.group("num")
    val = float(raw.replace(",", "")) if not raw.startswith(".") else float(raw)
    has_pct, has_mult = bool(m.group("pct")), bool(m.group("mult"))
    suf, scale = (m.group("suf") or "").lower(), (m.group("scale") or "").strip().lower()
    currency = bool(m.group("cur") or m.group("cur2"))
    opened, closed = bool(m.group("pneg")), bool(m.group("pclose"))
    # an opening bracket written just before a currency sign: "($4.6m)"
    if not opened and i > 0 and text[i - 1] == "(" and currency:
        opened, i = True, i - 1
        closed = closed or (j < len(text) and text[j:j + 1] == ")")
        if closed and text[j - 1:j] != ")":
            j += 1
    in_brackets = opened and closed
    line = text[text.rfind("\n", 0, i) + 1: (text.find("\n", j) if text.find("\n", j) >= 0 else len(text))]
    negative = bool(m.group("neg"))
    if in_brackets:
        inner = text[i:j]
        if "|" in line:
            negative = True                                        # table row
        elif suf:
            negative = False                                       # "($4.6m)": an aside
        elif currency or has_pct or "." in raw or "," in raw:
            negative = True
        else:
            word = re.search(r"([A-Za-z]+)\s*$", text[:i])
            if word and NUMBER_WORDS.get(word.group(1).lower()) == int(val):
                negative = False                                   # "five (5)"
            else:
                return Span(i, j, inner, "exclude", "item_number")
    plain4 = re.fullmatch(r"\d{4}", raw) and not (currency or has_pct or has_mult or suf or scale or m.group("neg"))
    if plain4 and 1990 <= int(raw) <= 2099 and not in_brackets:
        return Span(m.start("num"), m.end("num"), raw, "exclude", "year")
    if has_pct:
        val /= 100
    val *= SCALES.get(suf, 1.0) if not has_mult else 1.0
    val *= SCALES.get(scale, 1.0)
    if negative:
        val = -val
    return Span(i, j, text[i:j].strip(), "number", None, val)


def numbers(text: str) -> list[Span]:
    return [s for s in parse(text) if s.kind == "number"]


def close(a: float, b: float, tol: float) -> bool:
    return a == b if b == 0 else abs(a - b) / abs(b) <= tol


def lint_claim(text: str, refs: dict[str, float | None], tol: float) -> list[str]:
    """refs: id -> value of each item the claim references. Every number in the text must equal one of them within
    tol (after the normalisation above); returns the problems as feedback sentences."""
    out = []
    vals = {k: v for k, v in refs.items() if v is not None}
    for s in numbers(text):
        if not any(close(s.value, v, tol) for v in vals.values()):
            listed = ", ".join(f"{k} = {v!r}" for k, v in vals.items()) or "none"
            out.append(f'"{s.text}" (read as {s.value!r}) matches no value this claim references ({listed}); '
                       f"write the referenced value, or remove the number.")
    return out


def score_labelled(path) -> dict:
    """Linter precision on a labelled file (tests/fixtures/lint/labelled_numbers.jsonl). A linter number is a true
    positive when it overlaps a span labelled number, else a false positive; also lists labelled numbers the linter
    missed and true positives whose value differs from the label."""
    import json
    tp = fp = 0
    fps, missed, wrong, spans = [], [], [], 0
    for row in map(json.loads, open(path, encoding="utf-8")):
        gold, pos = [], 0
        for t in row["tokens"]:
            i = row["text"].find(t["span"], pos)
            gold.append({**t, "start": i, "end": i + len(t["span"])})
            pos = i + len(t["span"])
        spans += len(gold)
        got = numbers(row["text"])
        for s in got:
            hit = [g for g in gold if g["start"] < s.end and s.start < g["end"] and g["label"] == "number"]
            if hit:
                tp += 1
                if not close(s.value, hit[0]["value"], 1e-9):
                    wrong.append([row["id"], s.text, s.value, hit[0]["value"]])
            else:
                fp += 1
                fps.append([row["id"], s.text])
        for g in gold:
            if g["label"] == "number" and not any(s.start < g["end"] and g["start"] < s.end for s in got):
                missed.append([row["id"], g["span"]])
    return {"labelled_spans": spans, "linter_numbers": tp + fp, "true_positives": tp, "false_positives": fp,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None, "false_positive_spans": fps,
            "missed_numbers": missed, "wrong_values": wrong}
