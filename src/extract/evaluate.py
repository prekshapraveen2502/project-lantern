"""Part 5 item 2: field-level accuracy of each extraction strategy against the gold profile, with a bootstrap 95% CI
over fields (brief Part 5 acceptance). This is the only code that reads the gold file (a test enforces it).

Scoring per gold field type (Phase A section 4; the gold file's notes):
  number    within the field's tol_rel (default reconcile.tolerance_rel). Two scores: against truth (main), and
            against as_stated (the CIM's figure, or truth when the gold gives none); the gold comparator, when
            given, applies to as_stated and must equal the extracted comparator.
            addbacks.items.<slug>.amount: the first extracted add-back whose name contains every keyword.
  integer   exact; date: exact ISO date; text: lowercased, punctuation removed, whitespace collapsed, equal to truth
            or an alias.
  set_item  found if one item of the parent list contains every keyword (case-insensitive substring).
  absent    correct only if the extraction has no value.
Accuracy = correct / gold fields. Bootstrap: extract.bootstrap_resamples resamples of the gold fields with
replacement, seed extract.seed, percentile interval; the same resamples for every strategy, so paired differences
(b - a, c - b) get their own interval.

Selection rule for deal_profile.json, fixed before the record run (decision 2026-11-07 Q12): the strategy with the
highest truth accuracy; on a tie, the lower recorded cost; then the order single, staged, staged_reconciled.
Cost and latency come from data/bench/cs2/extract_profile.csv (written by the record run; cached calls are not
latency and make the strategy's latency null).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import numpy as np
import yaml

from src.extract.profile import Num, flatten

STRATEGIES = ("single", "staged", "staged_reconciled")
PAIRS = (("staged", "single"), ("staged_reconciled", "staged"))
LIST_PARENTS = {"gaps": "gaps", "business_model.revenue_streams": "business_model.revenue_streams",
                "business_model.sales_channels": "business_model.sales_channels"}
VENDOR_SINGLE_PROMPT_ACCURACY = 0.37          # the vendor's figure quoted in brief Part 5 item 2


def norm(s) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).split())


def close(a: float, b: float, tol: float) -> bool:
    return a == b if b == 0 else abs(a - b) / abs(b) <= tol


def has_all(text: str, keywords: list[str]) -> bool:
    t = str(text).lower()
    return all(k.lower() in t for k in keywords)


def lookup(g: dict, flat: dict):
    """The extracted value the gold field scores, or None."""
    i = g["id"]
    if i.startswith("addbacks.items."):
        return next((n for name, n in flat.get("addbacks.items", []) if has_all(name, g["keywords"])), None)
    if g["type"] == "set_item":
        parent = i.rsplit(".", 1)[0]
        items = flat.get(LIST_PARENTS.get(parent, parent), [])
        return next((x for x in items if has_all(x, g["keywords"])), None)
    return flat.get(i)


def score_field(g: dict, flat: dict, tol_default: float) -> dict:
    x = lookup(g, flat)
    t = g["type"]
    if t == "absent":
        ok = x is None
        return {"id": g["id"], "type": t, "extracted": None if x is None else _show(x), "truth": ok, "as_stated": ok}
    if x is None:
        return {"id": g["id"], "type": t, "extracted": None, "truth": False, "as_stated": False}
    if t == "number":
        tol = float(g.get("tol_rel", tol_default))
        v = x.value if isinstance(x, Num) else float(x)
        truth = close(v, float(g["truth"]), tol)
        stated = close(v, float(g.get("as_stated", g["truth"])), tol)
        if "comparator" in g:
            stated = stated and isinstance(x, Num) and x.comparator == g["comparator"]
    elif t == "integer":
        truth = stated = int(x) == int(g["truth"])
    elif t == "date":
        truth = stated = str(x) == str(g["truth"])
    elif t == "text":
        truth = stated = norm(x) in {norm(g["truth"]), *map(norm, g.get("aliases", []))}
    elif t == "set_item":
        truth = stated = True                              # lookup found an item with every keyword
    else:
        raise ValueError(f"{g['id']}: unknown gold type {t!r}")
    return {"id": g["id"], "type": t, "extracted": _show(x), "truth": truth, "as_stated": stated}


def _show(x):
    if isinstance(x, Num):
        return {"value": x.value, "comparator": x.comparator, "as_written": x.as_written, "ref": x.ref}
    return x


def bootstrap(correct: dict[str, np.ndarray], resamples: int, seed: int) -> dict:
    n = len(next(iter(correct.values())))
    idx = np.random.default_rng(seed).integers(0, n, size=(resamples, n))
    out = {}
    for s, arr in correct.items():
        boot = arr[idx].mean(axis=1)
        out[s] = {"accuracy": float(arr.mean()), "ci95": [float(np.percentile(boot, 2.5)),
                                                          float(np.percentile(boot, 97.5))]}
    for a, b in PAIRS:
        if a in correct and b in correct:
            d = (correct[a][idx] - correct[b][idx]).mean(axis=1)
            out[f"{a}-{b}"] = {"difference": float(correct[a].mean() - correct[b].mean()),
                               "ci95": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]}
    return out


def bench(path: Path, deal: str) -> dict:
    """Per strategy: calls, cost (USD), latency (seconds, sum over calls) and tokens from the record run."""
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r["deal"] == deal] if path.exists() else []
    out = {}
    for s in STRATEGIES:
        rs = [r for r in rows if r["strategy"] == s]
        if s == "staged_reconciled":
            rs = rs + [r for r in rows if r["strategy"] == "staged"]
        llm = [r for r in rs if r["section"] not in ("reconcile",) and not r["section"].startswith("part1_claims")]
        if not rs:
            out[s] = None
            continue
        secs = [float(r["seconds"]) for r in rs if r["seconds"] != ""]
        out[s] = {"llm_calls": len(llm), "usd": round(sum(float(r["usd"] or 0) for r in rs), 6),
                  "seconds": None if any(r["cached"] == "True" for r in llm) else round(sum(secs), 3),
                  "max_call_seconds": None if any(r["cached"] == "True" for r in llm) else
                  max(float(r["seconds"]) for r in llm),
                  "tokens_in": sum(int(r["tokens_in"] or 0) for r in rs),
                  "tokens_out": sum(int(r["tokens_out"] or 0) for r in rs), "run_at": rs[0]["run_at"]}
    return out


def select(acc: dict, cost: dict) -> str:
    def key(s):
        usd = (cost.get(s) or {}).get("usd")
        return (-acc[s]["accuracy"], float("inf") if usd is None else usd, STRATEGIES.index(s))
    return min(STRATEGIES, key=key)


def run(deal: str, profiles: dict, params: dict, out: Path, bench_path: Path, raw_profiles: dict | None = None) -> dict:
    """profiles: after the locate step (main result); raw_profiles: the same model outputs before it (numbers whose
    quote was not located are kept), scored with the same resamples (decision 2026-11-07 Q14)."""
    gold = yaml.safe_load(open(params["extract"]["gold"], encoding="utf-8"))
    if gold["deal_id"] != deal:
        raise NotImplementedError(f"no gold profile for {deal}; selection for other deals is not decided yet")
    tol = float(params["reconcile"]["tolerance_rel"])
    R, seed = int(params["extract"]["bootstrap_resamples"]), int(params["extract"]["seed"])
    score = lambda ps: {s: [score_field(g, flatten(ps[s]), tol) for g in gold["fields"]] for s in STRATEGIES}
    boot = lambda fs: {b: bootstrap({s: np.array([f[b] for f in fs[s]], dtype=float) for s in STRATEGIES}, R, seed)
                       for b in ("truth", "as_stated")}
    fields = score(profiles)
    res, n = boot(fields), len(gold["fields"])
    raw_fields = score(raw_profiles) if raw_profiles else None
    cost = bench(bench_path, deal)
    chosen = select(res["truth"], cost)
    report = {"deal_id": deal, "gold": params["extract"]["gold"], "n_fields": n,
              "vendor_single_prompt_accuracy": VENDOR_SINGLE_PROMPT_ACCURACY,
              "bootstrap": {"resamples": int(params["extract"]["bootstrap_resamples"]),
                            "seed": int(params["extract"]["seed"]), "interval": "percentile 95%"},
              "accuracy": res, "accuracy_raw": boot(raw_fields) if raw_fields else None,
              "fields_raw": raw_fields, "cost_latency": cost, "selected": chosen,
              "selection_rule": "highest truth accuracy; tie: lower recorded cost; then single, staged, "
                                "staged_reconciled",
              "fields": {s: fields[s] for s in STRATEGIES}}
    (out / "extraction_eval.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "deal_profile.json").write_text(profiles[chosen].model_dump_json(indent=1) + "\n", encoding="utf-8")
    return report
