"""The `figures` stage (brief Section 5): caption linkage (Part 6 item 3) and chart reads (item 4).

Outputs in data/figures_cs2/ (brief: data/figures/, which is CS1 layout's output dir; DECISIONS 2026-11-10):
  links.jsonl        one line per Figure block (src/figures/links.py)
  chart_reads.jsonl  one line per chart_read claim with its reconcile record (src/figures/chart_read.py)
Deals: params deal.practice plus deal.active, for each deal whose Part 0 records exist (today only Kestrel).
LLM calls go through src/llm/client.py with its cache pointed at data/figures_cache/llm; replay needs no network.
Usage: python -m src.figures.run [--deals kestrel] [--out data/figures_cs2]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import duckdb
import yaml

from src.facts.reconcile import load as load_table
from src.figures import chart_read as CR
from src.figures.links import link_deal, load_records

ROOT = Path("data/dataroom")


def deals_with_records(params: dict, wanted: list[str] | None) -> list[str]:
    names = wanted or [params["deal"]["practice"], *params["deal"]["active"]]
    return [d for d in names if (ROOT / d / "export" / "records.jsonl").exists()]


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, default=str) + "\n")


def use_figures_cache(client, root: Path = Path("data/figures_cache")) -> None:
    client.CACHE = Path(root) / "llm"
    client.CACHE.mkdir(parents=True, exist_ok=True)


def read_charts(deal: str, links: list[dict], params: dict, vocab: dict, client, db: Path) -> list[dict]:
    manifest = {r["accession"]: r for r in csv.DictReader(open(ROOT / deal / "export" / "manifest.csv"))}
    con = duckdb.connect(str(db), read_only=True)
    facts = load_table(con, "facts", deal, "AND tier = 3")
    con.close()
    fye = CR.fiscal_year_end(facts)
    latest_fy = max(f["fiscal_year"] for f in facts if f["fiscal_period"] == "FY")
    model = params["llm"]["models"]["vision"]
    cap = float(params["budget"]["usd_cap_per_task"])
    rm = CR.response_model(vocab)
    claims, log = [], []
    for link in links:
        if not link["chart_read_target"]:
            continue
        png = CR.figure_png(link).read_bytes()
        before = client.spent()
        read = client.structured(rm, CR.messages(link, png, vocab), model=model, stage=CR.STAGE,
                                 temperature=params["llm"]["temperature"])
        if client.spent() - before > cap:
            raise RuntimeError(f"{link['block_id']}: one chart read cost more than budget.usd_cap_per_task {cap}")
        rows = CR.to_claims(read, link, manifest[link["doc_id"]], hashlib.sha256(png).hexdigest(), vocab, fye,
                            latest_fy, int(params["facts"]["fye_tolerance_days"]),
                            f"{params['llm']['provider']}:{model}", log)
        print(f"  {link['doc_id']} {link['block_id']}: {read.title!r} metric={read.metric} scale={read.scale_word} "
              f"points={[(p.category, p.number, p.read_from) for p in read.points]}")
        claims += rows
    for entry in log:
        print(f"  dropped: {entry}")
    r = params["reconcile"]
    return CR.reconcile(claims, facts, float(r["tolerance_rel"]), float(r["high_gap_rel"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deals", nargs="*")
    ap.add_argument("--out", default="data/figures_cs2")
    ap.add_argument("--db", default="data/facts/facts.duckdb")
    a = ap.parse_args()
    params = yaml.safe_load(open("params.yaml"))
    vocab = yaml.safe_load(open("config/metrics_vocab.yaml", encoding="utf-8"))
    from src.llm import client
    use_figures_cache(client)
    links, reads = [], []
    for deal in deals_with_records(params, a.deals):
        rows = link_deal(load_records(ROOT / deal / "export" / "records.jsonl"))
        print(f"{deal}: {len(rows)} Figure blocks, captions {dict(Counter(r['caption_rule'] for r in rows))}, "
              f"chart-read targets {sum(r['chart_read_target'] for r in rows)}")
        links += rows
        got = read_charts(deal, rows, params, vocab, client, Path(a.db))
        print(f"{deal}: {len(got)} chart_read claims, reconcile "
              f"{dict(Counter(c['reconcile']['status'] for c in got))}")
        reads += got
    write_jsonl(links, Path(a.out) / "links.jsonl")
    write_jsonl(reads, Path(a.out) / "chart_reads.jsonl")


if __name__ == "__main__":
    main()
