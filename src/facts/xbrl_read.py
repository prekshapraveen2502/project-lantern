"""Part 1 item 2: read one filing's inline XBRL with Arelle (offline, project taxonomy cache) and group its tags.

read_filing()  every numeric, non-nil tag with its element id, contextRef, unitRef, unit measure, decimals,
               iXBRL scale, period and dims. Guard: the numeric facts Arelle loads must equal the ix:nonFraction
               tags in the HTML, or the taxonomy did not load (offline with an incomplete cache loses facts).
group_tags()   one fact per (concept, contextRef, unitRef): value and id from the most precise tag (highest
               decimals; ties: first in document order), the others' ids as also_tagged; a group is inconsistent
               when the precise value rounded to another tag's decimals differs from that tag's value.
CS1 src/xbrl.py is not used: its load_facts drops element ids, contextRefs and repeated tags.
"""
from __future__ import annotations

import json
import math
import re
from datetime import timedelta
from pathlib import Path


def unit_measure(numerators: list[str], denominators: list[str]) -> str:
    """iso4217:USD -> USD, xbrli:shares -> shares, xbrli:pure -> pure, USD per share -> USD/shares; others: qname."""
    def one(q: str) -> str:
        if q.startswith("iso4217:"):
            return q.split(":", 1)[1]
        return {"xbrli:shares": "shares", "xbrli:pure": "pure"}.get(q, q)
    num = "*".join(one(q) for q in numerators)
    return f"{num}/{'*'.join(one(q) for q in denominators)}" if denominators else num


def decimals_value(raw: str | None) -> float:
    """For ranking precision: INF is the most precise, missing the least."""
    if raw is None or raw == "":
        return -math.inf
    return math.inf if str(raw).upper() == "INF" else float(raw)


def period_dates(is_instant: bool, start_dt, end_dt) -> tuple:
    """(start, end) as dates. Arelle's end and instant datetimes are exclusive (midnight after the last day), so one
    day is subtracted from both: an instant tagged 2026-06-30 comes back as 2026-07-01 00:00 and is stored as 06-30."""
    end = (end_dt - timedelta(days=1)).date()
    return (None if is_instant else start_dt.date()), end


def read_filing(htm: Path, cache_dir: Path) -> dict:
    """Returns {tags, loaded, ix_nonfraction, nil, unconvertible}. Raises if the guard fails.
    loaded = len(tags) + nil + len(unconvertible): nil tags (or tags with no context/unit) carry no value;
    unconvertible tags are numbers Arelle cannot convert (ixt-sec:numwordsen, e.g. "one": arelle-release has no SEC
    transformation registry). Unconvertible tags are returned with concept, element id, text and format, not stored."""
    from arelle import Cntlr

    n_ix = len(re.findall(r"<ix:nonFraction\b", Path(htm).read_text(encoding="utf-8", errors="ignore"), re.I))
    cntlr = Cntlr.Cntlr(logFileName="logToBuffer")
    cntlr.webCache.cacheDir = str(Path(cache_dir).resolve())
    cntlr.webCache.workOffline = True            # never download: the DVC-tracked cache is the only source
    model = cntlr.modelManager.load(str(htm))
    if model is None:
        raise RuntimeError(f"Arelle could not load {htm}")
    numeric = [f for f in model.facts if getattr(f, "isNumeric", False)]
    if len(numeric) != n_ix:
        raise RuntimeError(f"{htm}: Arelle loaded {len(numeric)} numeric facts but the HTML has {n_ix} "
                           f"ix:nonFraction tags; taxonomy not loaded (check {cache_dir})")
    tags, unconvertible, nil = [], [], 0
    for order, f in enumerate(numeric):
        if f.isNil or f.context is None or f.unit is None:
            nil += 1
            continue
        if f.xValue is None:
            fmt = f.format
            unconvertible.append({"concept": str(f.qname), "element_id": f.id, "text": (f.text or "").strip(),
                                  "format": f"{fmt.prefix}:{fmt.localName}" if fmt is not None else None})
            continue
        c = f.context
        start, end = period_dates(c.isInstantPeriod, c.startDatetime, c.endDatetime)
        dims = {}
        for dim_qn, dv in c.qnameDims.items():
            member = getattr(dv, "memberQname", None)
            dims[str(dim_qn)] = str(member) if member is not None else str(getattr(dv, "typedMember", ""))
        num, den = f.unit.measures
        tags.append({
            "concept": str(f.qname), "value": float(f.xValue), "decimals": f.decimals,
            "scale": int(f.get("scale") or 0), "unit": unit_measure([str(q) for q in num], [str(q) for q in den]),
            "unit_ref": f.unitID, "context_ref": f.contextID, "element_id": f.id, "order": order,
            "text": "".join(f.itertext()).strip(), "sign": f.get("sign"),   # nested tags: text is in the inner tag
            "period_type": "instant" if start is None else "duration", "start": start, "end": end,
            "dims": json.dumps(dims, sort_keys=True),
        })
    cntlr.close()
    if len(numeric) != len(tags) + nil + len(unconvertible):
        raise RuntimeError(f"{htm}: loaded {len(numeric)} != stored {len(tags)} + nil {nil} + "
                           f"unconvertible {len(unconvertible)}")
    return {"tags": tags, "loaded": len(numeric), "ix_nonfraction": n_ix, "nil": nil, "unconvertible": unconvertible}


def citation_value(text: str, scale: int, sign: str | None) -> float | None:
    """The number a reader sees, back in base units: (-1 if sign == "-" else 1) x text x 10^scale.
    None when the text is not a plain number (e.g. a dash for zero), so the check cannot apply."""
    s = text.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"(\d+(\.\d*)?|\.\d+)", s):
        return None
    return (-1.0 if sign == "-" else 1.0) * float(s) * 10 ** scale


def check_unconvertible(unconvertible: list[dict], vocab_concepts: set[str], where: str) -> None:
    """Stop if a concept listed in metrics_vocab.yaml could not be converted; other concepts are skipped and logged."""
    hit = sorted({u["concept"] for u in unconvertible if u["concept"] in vocab_concepts})
    if hit:
        raise RuntimeError(f"{where}: vocabulary concept(s) {hit} written in a form Arelle cannot convert "
                           f"(e.g. {next(u for u in unconvertible if u['concept'] in vocab_concepts)})")


def group_tags(tags: list[dict]) -> tuple[list[dict], list[dict]]:
    """(facts, inconsistent groups). Each fact is its most precise tag plus also_tagged (ids of the others)."""
    groups: dict[tuple, list[dict]] = {}
    for t in tags:
        groups.setdefault((t["concept"], t["context_ref"], t["unit_ref"]), []).append(t)
    facts, inconsistent = [], []
    for key, group in groups.items():
        best = max(group, key=lambda t: (decimals_value(t["decimals"]), -t["order"]))
        others = sorted((t for t in group if t is not best), key=lambda t: t["order"])
        bad = []
        for t in others:
            d = decimals_value(t["decimals"])
            if math.isfinite(d) and round(best["value"], int(d)) != t["value"]:
                bad.append({"element_id": t["element_id"], "value": t["value"], "decimals": t["decimals"]})
            elif d == math.inf and t["value"] != best["value"]:
                bad.append({"element_id": t["element_id"], "value": t["value"], "decimals": t["decimals"]})
        if bad:
            inconsistent.append({"concept": key[0], "context_ref": key[1], "unit_ref": key[2],
                                 "element_id": best["element_id"], "value": best["value"],
                                 "decimals": best["decimals"], "differs": bad})
        facts.append({**best, "also_tagged": [t["element_id"] for t in others]})
    facts.sort(key=lambda f: f["order"])
    return facts, inconsistent
