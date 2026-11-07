"""Part 4 (decision Q1 a): the minimal calculator the answering service needs before Part 6. Only growth,
ratio/share and difference; Part 6 adds LTM, net debt, EV and multiples to this interface.

Every output carries a calc_id and a trace of its input fact_ids. calc_id = "CALC-" + the first 12 hex digits of
sha256 over (op, input fact_ids in argument order), so the same calculation on the same facts always has the same
id. A derived value inherits the lowest tier of its inputs (brief Section 5: the highest tier number). Outputs are
kept in a registry, outputs/qa/calcs.jsonl, one JSON object per calc_id; GET /calc/{calc_id} reads it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

OPS = ("growth", "ratio", "difference")


class CalcError(ValueError):
    """An input the calculator cannot use (missing value, zero denominator)."""


def calc_id(op: str, input_ids: list[str]) -> str:
    return "CALC-" + hashlib.sha256(json.dumps([op, list(input_ids)]).encode()).hexdigest()[:12]


def _value(f: dict) -> float:
    if f.get("value") is None:
        raise CalcError(f"{f.get('fact_id')}: no value")
    return float(f["value"])


def _out(op: str, inputs: list[dict], value: float, formula: str) -> dict:
    ids = [f["fact_id"] for f in inputs]
    return {"calc_id": calc_id(op, ids), "op": op, "value": value, "unit": "pure" if op != "difference" else
            inputs[0].get("unit"), "tier": max(int(f["tier"]) for f in inputs), "formula": formula,
            "trace": [{"fact_id": f["fact_id"], "value": _value(f), "tier": int(f["tier"])} for f in inputs]}


def growth(current: dict, prior: dict) -> dict:
    """current / prior - 1."""
    p = _value(prior)
    if p == 0:
        raise CalcError(f"{prior['fact_id']}: prior value is zero")
    return _out("growth", [current, prior], _value(current) / p - 1, "a / b - 1")


def ratio(numerator: dict, denominator: dict) -> dict:
    """numerator / denominator (a share when the numerator is part of the denominator)."""
    d = _value(denominator)
    if d == 0:
        raise CalcError(f"{denominator['fact_id']}: denominator is zero")
    return _out("ratio", [numerator, denominator], _value(numerator) / d, "a / b")


def difference(a: dict, b: dict) -> dict:
    """a - b, in a's unit; both inputs must share a unit."""
    if a.get("unit") != b.get("unit"):
        raise CalcError(f"{a['fact_id']} and {b['fact_id']} have different units")
    return _out("difference", [a, b], _value(a) - _value(b), "a - b")


def load(registry: Path) -> dict[str, dict]:
    if not Path(registry).exists():
        return {}
    return {r["calc_id"]: r for r in (json.loads(l) for l in open(registry, encoding="utf-8") if l.strip())}


def register(calcs: list[dict], registry: Path) -> int:
    """Append calcs not yet in the registry; returns how many were added."""
    have = load(registry)
    new = []
    for c in calcs:
        if c["calc_id"] not in have:
            have[c["calc_id"]] = c
            new.append(c)
    if new:
        Path(registry).parent.mkdir(parents=True, exist_ok=True)
        with open(registry, "a", encoding="utf-8") as f:
            for c in new:
                f.write(json.dumps(c, sort_keys=True) + "\n")
    return len(new)
