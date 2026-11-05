"""Part 1 item 1: the controlled vocabulary in config/metrics_vocab.yaml.

load_vocab()       read the file and check it (kinds, unit types, concept prefixes, patterns compile)
normalise_label()  apply the file's label rules to a data-room row label
match_line()       map a row label to one metric: None if no metric matches, ValueError if two do
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

DEFAULT_PATH = Path("config/metrics_vocab.yaml")
KINDS = {"statement", "revenue_build", "derived", "ebitda_bridge", "kpi"}
UNIT_TYPES = {"currency", "percent", "ratio", "count"}
NO_XBRL_KINDS = {"revenue_build", "ebitda_bridge", "kpi"}


def load_vocab(path: Path | str = DEFAULT_PATH) -> dict:
    vocab = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    errs = []
    for name, m in vocab["metrics"].items():
        if m.get("kind") not in KINDS:
            errs.append(f"{name}: kind {m.get('kind')!r} not in {sorted(KINDS)}")
        if m.get("unit_type") not in UNIT_TYPES:
            errs.append(f"{name}: unit_type {m.get('unit_type')!r} not in {sorted(UNIT_TYPES)}")
        if m.get("period") not in ("instant", "duration"):
            errs.append(f"{name}: period must be instant or duration")
        if m.get("sign", "signed") not in ("cost", "signed"):
            errs.append(f"{name}: sign must be cost or signed")
        if m.get("xbrl_select", "first") not in ("first", "all"):
            errs.append(f"{name}: xbrl_select must be first or all")
        if m.get("xbrl") and m.get("kind") in NO_XBRL_KINDS:
            errs.append(f"{name}: kind {m['kind']} has no XBRL concepts")
        errs += [f"{name}: concept {c!r} needs a us-gaap: or dei: prefix"
                 for c in m.get("xbrl", []) if not c.startswith(("us-gaap:", "dei:"))]
        try:
            m["_patterns"] = [re.compile(p, re.IGNORECASE) for p in m.get("dataroom_lines", [])]
        except re.error as e:
            errs.append(f"{name}: bad pattern ({e})")
    if errs:
        raise ValueError("metrics_vocab.yaml: " + "; ".join(errs))
    return vocab


def normalise_label(label: str, rules: dict) -> str:
    s = str(label)
    if rules.get("straight_apostrophes"):
        s = s.replace("’", "'").replace("‘", "'")
    if rules.get("lowercase"):
        s = s.lower()
    if rules.get("collapse_whitespace"):
        s = " ".join(s.split())
    s = s.strip()
    stripped = True
    while stripped:                       # "+ add: x" -> "x"
        stripped = False
        for p in rules.get("strip_prefixes", []):
            if s.startswith(p):
                s, stripped = s[len(p):].strip(), True
    if rules.get("strip_trailing_colon"):
        s = s.rstrip(":").rstrip()
    return s


def match_line(label: str, vocab: dict) -> str | None:
    norm = normalise_label(label, vocab["normalise"])
    hits = [name for name, m in vocab["metrics"].items() if any(p.search(norm) for p in m["_patterns"])]
    if len(hits) > 1:
        raise ValueError(f"row label {label!r} (normalised {norm!r}) matches several metrics: {hits}")
    return hits[0] if hits else None
