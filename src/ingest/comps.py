"""ingest_comps (Part 0): for every ticker in comps.tickers, save the companyfacts XBRL
history and the latest filing of each form in comps.forms within [comps.after, comps.before),
then unpack each filing with the CS1 unpacker.

Usage:  python -m src.ingest.comps --output data/edgar_comps
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.ingest import edgar
from download import unpack_all          # CS1 code, unchanged (src/ is on sys.path)
from xbrl import find_ixbrl, load_facts  # CS1 code, unchanged
from docling_parse import build_converter  # CS1 code, unchanged
from export import ITEM                    # CS1 Item-heading regex, unchanged
from src.ingest.runstats import RunStats

WANTED_ITEMS = ("1", "1A", "7")   # brief Part 0: Business, Risk Factors, MD&A (10-K numbering)

def split_items(markdown: str, max_chars: int) -> dict[str, str]:
    """Cut 10-K Markdown at short 'Item N.' heading lines (the CS1 ITEM regex).
    The table of contents repeats every Item heading, so keep the longest section per Item."""
    lines = markdown.splitlines()
    starts = []
    for i, line in enumerate(lines):
        bare = line.strip().lstrip("#").strip().strip("*_").strip()
        if bare and len(bare) <= max_chars:
            m = ITEM.match(bare)
            if m:
                starts.append((i, m.group(1).upper()))
    best: dict[str, str] = {}
    for n, (i, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        text = "\n".join(lines[i:end]).strip()
        if len(text) > len(best.get(key, "")):
            best[key] = text
    return best


def write_docling_items(fdir: Path, doc: str, conv, max_chars: int) -> dict[str, int]:
    """Docling HTML path: primary 10-K HTML -> Markdown -> items/item_1.md, item_1a.md, item_7.md."""
    htm = fdir / "unpacked" / doc
    markdown = conv.convert(str(htm)).document.export_to_markdown()
    sections = split_items(markdown, max_chars)
    missing = [k for k in WANTED_ITEMS if k not in sections]
    if missing:
        raise RuntimeError(f"{htm.name}: Items not found: {missing}")
    idir = fdir / "items"
    idir.mkdir(exist_ok=True)
    sizes = {}
    for k in WANTED_ITEMS:
        (idir / f"item_{k.lower()}.md").write_text(sections[k] + "\n", encoding="utf-8")
        sizes[k] = len(sections[k])
    return sizes


def ingest_ticker(ticker: str, cik: str, params: dict, out: Path, conv) -> list[Path]:
    c = params["comps"]
    tdir = out / ticker
    tdir.mkdir(parents=True, exist_ok=True)

    facts = edgar.companyfacts(cik, params)
    (tdir / "companyfacts.json").write_text(json.dumps(facts), encoding="utf-8")

    subs = edgar.submissions(cik, params)
    filing_dirs = []
    for form in c["forms"]:
        f = edgar.latest_filing(subs, form, c["after"], c["before"])
        if f is None:
            raise RuntimeError(f"{ticker}: no {form} filed in [{c['after']}, {c['before']})")
        fdir = tdir / form / f["accn"]
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "full-submission.txt").write_bytes(edgar.full_submission(cik, f["accn"], params))
        (fdir / "filing.json").write_text(json.dumps({**f, "ticker": ticker, "cik": cik}, indent=2),
                                          encoding="utf-8")
        print(f"[INFO] {ticker} {form} {f['date']} {f['accn']}")
        filing_dirs.append(fdir)

    unpack_all(filing_dirs)
    for fdir in filing_dirs:
        doc = json.loads((fdir / "filing.json").read_text(encoding="utf-8"))["doc"]
        n = write_xbrl_facts(fdir, doc)
        print(f"[INFO] {ticker} {fdir.parent.name} {n} XBRL facts (Arelle)")
        if fdir.parent.name == "10-K":
            sizes = write_docling_items(fdir, doc, conv, params["export"]["item_heading_max_chars"])
            print(f"[INFO] {ticker} 10-K Docling Items (chars): {sizes}")
    return filing_dirs

def primary_ixbrl(fdir: Path, doc: str) -> Path:
    """EDGAR's primaryDocument is the 10-K/10-Q itself; check it really is iXBRL.
    Fall back to CS1's find_ixbrl only if it is not, and say so."""
    unpacked = fdir / "unpacked"
    p = unpacked / doc
    if p.exists() and "ix:header" in p.read_text(encoding="utf-8", errors="ignore"):
        return p
    found = find_ixbrl(unpacked)
    if found is None:
        raise RuntimeError(f"no iXBRL document in {unpacked}")
    print(f"[WARN] primary document {doc} is not iXBRL; using {found.name}")
    return found


def write_xbrl_facts(fdir: Path, doc: str) -> int:
    """Arelle facts from the filing's own iXBRL (CS1 load_facts), one JSON object per line."""
    htm = primary_ixbrl(fdir, doc)
    facts, _labels, _periods = load_facts(htm)
    with open(fdir / "xbrl_facts.jsonl", "w", encoding="utf-8") as fh:
        for fact in facts:
            fh.write(json.dumps(fact) + "\n")
    return len(facts)


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 0 ingest_comps")
    ap.add_argument("--output", default="data/edgar_comps")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--stats", default=None, help="run-stats CSV (default data/bench/cs2/ingest_comps.csv)")
    args = ap.parse_args()

    params = edgar.load_params(args.params)
    out = Path(args.output)
    ciks = edgar.cik_for(params["comps"]["tickers"], params)
    conv = build_converter(params.get("docling", {}))   # CS1 docling: settings
    stats = RunStats("ingest_comps", args.stats)
    for ticker, cik in ciks.items():
        print(f"[INFO] {ticker} CIK {cik}")
        with stats.item(ticker, "filings") as r:
            r["units"] = len(ingest_ticker(ticker, cik, params, out, conv))
    stats.finish()


if __name__ == "__main__":
    main()
