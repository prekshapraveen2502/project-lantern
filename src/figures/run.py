"""The `figures` stage (brief Section 5): caption linkage (Part 6 item 3) -> data/figures_cs2/links.jsonl (brief: data/figures/, which is CS1 layout's output dir; DECISIONS 2026-11-10).

Deals: params deal.practice plus deal.active, for each deal whose Part 0 records exist (today only Kestrel).
Usage: python -m src.figures.run [--deals kestrel] [--out data/figures_cs2]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import yaml

from src.figures.links import link_deal, load_records

ROOT = Path("data/dataroom")


def deals_with_records(params: dict, wanted: list[str] | None) -> list[str]:
    names = wanted or [params["deal"]["practice"], *params["deal"]["active"]]
    return [d for d in names if (ROOT / d / "export" / "records.jsonl").exists()]


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deals", nargs="*")
    ap.add_argument("--out", default="data/figures_cs2")
    a = ap.parse_args()
    params = yaml.safe_load(open("params.yaml"))
    links = []
    for deal in deals_with_records(params, a.deals):
        rows = link_deal(load_records(ROOT / deal / "export" / "records.jsonl"))
        print(f"{deal}: {len(rows)} Figure blocks, captions {dict(Counter(r['caption_rule'] for r in rows))}, "
              f"chart-read targets {sum(r['chart_read_target'] for r in rows)}")
        links += rows
    write_jsonl(links, Path(a.out) / "links.jsonl")


if __name__ == "__main__":
    main()
