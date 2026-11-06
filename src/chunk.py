"""Part 2 items 1-2: the `chunk` stage. Every strategy in params chunk.grid -> data/chunks/{strategy}/{size}_{overlap}.jsonl.

Corpus (DECISIONS 2026-11-06, Part 2 P1-P7): Kestrel's Part 0 records (data/dataroom/{deal}/export/records.jsonl) and
each comparable's Docling Items 1, 1A, 7 (data/edgar_comps/{T}/10-K/{accn}/items/item_*.md). Earnings HTML and CS1's
AAPL records are not indexed. Units of text ("blocks"):
  data room   the Part 0 records (Figure blocks and records of hidden xlsx sheets left out); section = the CS1 layout
              section with (cid:N) tokens removed, the sheet name for xlsx, "transcript" for the transcript
  filings     each blank-line-separated paragraph or Markdown table of an Item; block_id "{item}_b{nnnn}";
              section = "Item 1" / "Item 1A" / "Item 7"
Strategies (token budgets counted with the bge-small tokenizer in data/tokenizer/, no model weights):
  block       merge consecutive text blocks within a section up to the budget; a block longer than the budget is
              split on its own; tables are never merged with text
  section     pack a section's text across paragraph boundaries (sentence, then space separators) and prefix every
              chunk with the section heading, counted in the budget: filings "Item N | <the Item's first line>";
              CIM/deck/contracts/QoE the CS1 section title (cid-cleaned); transcript its first line (the title)
  recursive   the baseline: the document's text split by RecursiveCharacterTextSplitter (budget, overlap)
Every strategy indexes tables as their own chunks: caption + scale + header row + rows; a table longer than the
budget is split into row groups that each repeat the caption, scale and header, all linked to the same block_id.
Table rows are compacted in the chunk text only (empty cells dropped, consecutive repeated cells merged); records,
blocks and block text are unchanged. A row still over the budget stops the stage.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

import yaml

CID = re.compile(r"\(cid:\d+\) ?")
ITEM_FILES = {"1": "item_1.md", "1A": "item_1a.md", "7": "item_7.md"}
FY_CELL = re.compile(r"^FY\d{4}$")


class ChunkError(ValueError):
    """A case the chunk rules do not cover: the stage stops."""


def load_tokenizer(path: Path):
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(str(path / "tokenizer.json"))
    return lambda text: len(tok.encode(text, add_special_tokens=False).ids)


# ---------- blocks ----------
def dataroom_blocks(export: Path, raw: Path) -> tuple[list[dict], list[dict]]:
    """(blocks, hidden sheets left out). Each block: doc_id, deal_id, source_type, tier, page, section, block_id,
    kind (text|table), text, rows (tables), caption."""
    import openpyxl
    manifest = {m["accession"]: m for m in csv.DictReader(open(export / "manifest.csv", encoding="utf-8"))}
    records = [json.loads(line) for line in open(export / "records.jsonl", encoding="utf-8")]
    hidden, left_out = {}, []
    for doc, m in manifest.items():
        if m["file"].lower().endswith(".xlsx"):
            wb = openpyxl.load_workbook(raw / m["file"], read_only=True)
            hidden[doc] = {ws.title for ws in wb.worksheets if ws.sheet_state != "visible"}
    blocks, section, last_text, doc_title = [], {}, {}, {}
    for r in records:
        doc = r["doc_id"]
        if r["block_type"] == "Figure":
            continue
        if r["block_type"] == "Table" and r["section"] in hidden.get(doc, set()):
            left_out.append({"doc_id": doc, "block_id": r["block_id"], "sheet": r["section"]})
            continue
        form = manifest[doc]["form"]
        if r["citation"]["kind"] == "text_span":
            sec = "transcript"
            heading = doc_title.setdefault(doc, (r["text"] or "").strip() or form)   # the transcript's first line
        else:
            if r["section"]:
                section[doc] = CID.sub("", r["section"]).strip()
            sec = section.get(doc, form)
            heading = sec
        base = {"doc_id": doc, "deal_id": r["deal_id"], "source_type": r["source_type"], "tier": r["tier"],
                "page": r["page"], "section": sec, "block_id": r["block_id"], "heading": heading}
        if r["block_type"] == "Table":
            rows, caption, scale_rows = table_rows(r, last_text.get((doc, r["page"])))
            blocks.append({**base, "kind": "table", "rows": rows, "caption": caption, "scale_rows": scale_rows,
                           "text": r["text"]})
        elif (r["text"] or "").strip():
            blocks.append({**base, "kind": "text", "text": r["text"]})
            if r["block_type"] in ("Title", "Text"):
                last_text[(doc, r["page"])] = r["text"]
    return blocks, left_out


def table_rows(r: dict, preceding: str | None) -> tuple[list[list[str]], str | None, list[str]]:
    """(rows as text cells, caption, text above the header). xlsx: caption = the sheet's title cell; header = the row
    holding FYyyyy labels. PDF tables: caption = the nearest preceding Title/Text block on the page; header = row 1."""
    t = r.get("table") or {}
    cell = lambda c: "" if c is None else str(c)
    if r["citation"]["kind"] == "sheet_cell":
        allrows = [[cell(c) for c in t.get("columns", [])]] + [[cell(c) for c in row] for row in t.get("rows", [])]
        hdr = next((i for i, row in enumerate(allrows) if any(FY_CELL.match(c.strip()) for c in row)), 0)
        above = [" ".join(c for c in row if c) for row in allrows[:hdr]]
        caption = above[0] if above else r["section"]
        rows = [row for row in allrows[hdr:] if any(c.strip() for c in row)]
        return rows, caption, above
    rows = [[cell(c) for c in row] for row in (t.get("rows") or [])]
    if not rows:                                       # no accepted Camelot table: one row from the record text
        rows = [[line] for line in (r["text"] or "").splitlines() if line.strip()]
    return rows, preceding, []


def filing_blocks(comps_dir: Path, tickers: list[str]) -> list[dict]:
    blocks = []
    for t in tickers:
        fdir = next((comps_dir / t / "10-K").iterdir())
        meta = json.loads((fdir / "filing.json").read_text(encoding="utf-8"))
        for item, fname in ITEM_FILES.items():
            md = (fdir / "items" / fname).read_text(encoding="utf-8")
            paras = [p.strip() for p in re.split(r"\n\s*\n", md) if p.strip()]
            first_line = next((l.strip() for l in md.splitlines() if l.strip()), "")
            heading = f"Item {item} | {first_line}"
            prev = None
            for n, p in enumerate(paras, 1):
                base = {"doc_id": meta["accn"], "deal_id": "public", "source_type": "edgar_filing", "tier": 2,
                        "page": None, "section": f"Item {item}", "block_id": f"{item}_b{n:04d}", "heading": heading}
                lines = p.splitlines()
                if all(l.lstrip().startswith("|") for l in lines):
                    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines
                            if not re.fullmatch(r"\|?[\s:|-]+\|?", l.strip())]
                    caption = prev if prev and len(prev) <= 200 else None
                    blocks.append({**base, "kind": "table", "rows": rows, "caption": caption, "scale_rows": [],
                                   "text": p})
                else:
                    blocks.append({**base, "kind": "text", "text": p})
                prev = p if not p.startswith("|") else None
    return blocks


# ---------- splitting ----------
DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
SENTENCE_SEPARATORS = [". ", " ", ""]


def splitter(budget: int, overlap: int, count, separators: list[str] = DEFAULT_SEPARATORS):
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    return RecursiveCharacterTextSplitter(chunk_size=budget, chunk_overlap=overlap, length_function=count,
                                          separators=separators, add_start_index=True)


def split_text(text: str, budget: int, overlap: int, count,
               separators: list[str] = DEFAULT_SEPARATORS) -> list[tuple[int, str]]:
    """[(start offset, piece)] with every piece within the budget."""
    out = [(d.metadata["start_index"], d.page_content)
           for d in splitter(budget, overlap, count, separators).create_documents([text])]
    for _, piece in out:
        if count(piece) > budget:
            raise ChunkError(f"a piece of {count(piece)} tokens exceeds the budget {budget}")
    return out


def scale_of(texts: list[str], phrases: dict) -> str | None:
    for t in texts:
        for w in re.findall(r"[a-z]+", (t or "").lower()):
            if w in phrases:
                return w
    return None


def compact(row: list[str]) -> list[str]:
    """Chunk text only: drop empty cells and merge a cell that repeats the previous one (Docling writes a merged
    cell's text once per spanned column). Side effect: genuinely equal adjacent cells are merged too."""
    out = []
    for c in row:
        if c.strip() and (not out or c != out[-1]):
            out.append(c)
    return out


def table_chunks(b: dict, budget: int, count, phrases: dict) -> list[dict]:
    """Caption, scale, header row and rows (compacted copies; the block is not changed); split into row groups that
    repeat the prefix when over the budget."""
    rows = [compact(r) for r in b["rows"]]
    header, body = (rows[0], rows[1:]) if rows else ([], [])
    scale = scale_of([b.get("caption") or "", " ".join(header)] + b.get("scale_rows", []), phrases)
    prefix = "\n".join(x for x in (b.get("caption"), f"Scale: {scale}" if scale else None,
                                   " | ".join(header)) if x)
    line = lambda row: " | ".join(row)
    if count(prefix) >= budget:
        raise ChunkError(f"{b['doc_id']}:{b['block_id']}: table caption+header alone exceed the budget")
    groups, cur = [], []
    for row in body:
        trial = "\n".join([prefix] + [line(r) for r in cur + [row]])
        if cur and count(trial) > budget:
            groups.append(cur)
            cur = []
        if count("\n".join([prefix, line(row)])) > budget:
            raise ChunkError(f"{b['doc_id']}:{b['block_id']}: one table row exceeds the budget")
        cur.append(row)
    groups.append(cur)
    return [{"kind": "table", "text": "\n".join([prefix] + [line(r) for r in g]), "blocks": [b], "part": i + 1,
             "parts": len(groups)} for i, g in enumerate(groups)]


def text_pieces(strategy: str, blocks: list[dict], budget: int, overlap: int, count) -> list[dict]:
    """Text chunks of one document (tables excluded) for one strategy; each lists the blocks it covers."""
    texts = [b for b in blocks if b["kind"] == "text"]
    out = []
    if strategy == "block":
        cur, cur_sec = [], None
        def flush():
            if cur:
                out.append({"kind": "text", "text": "\n\n".join(x["text"] for x in cur), "blocks": list(cur)})
        for b in texts:
            n = count(b["text"])
            if n > budget:                                       # an oversize block is split on its own
                flush(); cur.clear()
                out += [{"kind": "text", "text": p, "blocks": [b]} for _, p in split_text(b["text"], budget, 0, count)]
                cur_sec = None
                continue
            joined = "\n\n".join([x["text"] for x in cur] + [b["text"]])
            if cur and (b["section"] != cur_sec or count(joined) > budget):
                flush(); cur.clear()
            cur.append(b)
            cur_sec = b["section"]
        flush()
        return out
    groups = {}
    if strategy == "section":
        for b in texts:
            groups.setdefault(b["section"], []).append(b)
    elif strategy == "recursive":
        groups[None] = texts
    else:
        raise ChunkError(f"unknown strategy {strategy}")
    for _, bs in groups.items():
        spans, pos, parts = [], 0, []
        for b in bs:
            spans.append((pos, pos + len(b["text"]), b))
            parts.append(b["text"])
            pos += len(b["text"]) + 2
        full = "\n\n".join(parts)
        if strategy == "section":                  # pack across paragraphs; every chunk carries the section heading
            heading = bs[0]["heading"]
            room = budget - count(heading + "\n")
            if room <= 0:
                raise ChunkError(f"{bs[0]['doc_id']}: heading {heading!r} alone exceeds the budget {budget}")
            pieces = [(s, p, f"{heading}\n{p}") for s, p in split_text(full, room, 0, count, SENTENCE_SEPARATORS)]
        else:
            pieces = [(s, p, p) for s, p in split_text(full, budget, overlap, count)]
        for start, body, chunk_text in pieces:
            end = start + len(body)
            out.append({"kind": "text", "text": chunk_text,
                        "blocks": [b for s, e, b in spans if s < end and e > start]})
    return out


def chunk_set(blocks: list[dict], strategy: str, budget: int, overlap: int, count, phrases: dict) -> list[dict]:
    by_doc: dict = {}
    for b in blocks:
        by_doc.setdefault(b["doc_id"], []).append(b)
    rows = []
    for doc, bs in by_doc.items():
        pieces = text_pieces(strategy, bs, budget, overlap, count)
        for b in bs:
            if b["kind"] == "table":
                pieces += table_chunks(b, budget, count, phrases)
        for n, p in enumerate(pieces, 1):
            first = p["blocks"][0]
            rows.append({
                "chunk_id": hashlib.sha256(f"{strategy}|{budget}|{overlap}|{doc}|{n}".encode()).hexdigest()[:16],
                "strategy": strategy, "size": budget, "overlap": overlap, "kind": p["kind"],
                "deal_id": first["deal_id"], "doc_id": doc, "source_type": first["source_type"], "tier": first["tier"],
                "page": first["page"], "section": first["section"],
                "block_ids": [b["block_id"] for b in p["blocks"]], "tokens": count(p["text"]), "text": p["text"],
                **({"table_part": p["part"], "table_parts": p["parts"]} if p["kind"] == "table" else {}),
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 2 chunk stage")
    ap.add_argument("--deals", nargs="*", default=[])
    ap.add_argument("--dataroom", default="data/dataroom")
    ap.add_argument("--comps", default="data/edgar_comps")
    ap.add_argument("--tokenizer", default="data/tokenizer/bge-small-en-v1.5")
    ap.add_argument("--vocab", default="config/metrics_vocab.yaml")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--out", default="data/chunks")
    args = ap.parse_args()
    params = yaml.safe_load(Path(args.params).read_text(encoding="utf-8"))
    phrases = yaml.safe_load(Path(args.vocab).read_text(encoding="utf-8"))["scale_phrases"]
    count = load_tokenizer(Path(args.tokenizer))
    blocks, left_out = filing_blocks(Path(args.comps), params["comps"]["tickers"]), []
    for deal in args.deals:
        b, lo = dataroom_blocks(Path(args.dataroom) / deal / "export", Path(args.dataroom) / deal / "raw")
        if any(x["deal_id"] != deal for x in b):
            raise ChunkError(f"{deal}: a record's deal_id differs from its folder")
        blocks += b
        left_out += lo
    out = Path(args.out)
    log = {"blocks": len(blocks), "hidden_sheets_left_out": left_out, "sets": {}}
    for strategy, grid in params["chunk"]["grid"].items():
        (out / strategy).mkdir(parents=True, exist_ok=True)
        for budget, overlap in grid:
            rows = chunk_set(blocks, strategy, budget, overlap, count, phrases)
            with open(out / strategy / f"{budget}_{overlap}.jsonl", "w", encoding="utf-8", newline="\n") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            log["sets"][f"{strategy}/{budget}_{overlap}"] = {
                "chunks": len(rows), "table_chunks": sum(r["kind"] == "table" for r in rows),
                "max_tokens": max(r["tokens"] for r in rows), "tokens": sum(r["tokens"] for r in rows)}
            print(f"[INFO] chunk {strategy}/{budget}_{overlap}: {log['sets'][f'{strategy}/{budget}_{overlap}']}")
    (out / "chunk_log.json").write_text(json.dumps(log, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[INFO] chunk: {len(blocks)} blocks; hidden sheets left out: {left_out}")


if __name__ == "__main__":
    main()
