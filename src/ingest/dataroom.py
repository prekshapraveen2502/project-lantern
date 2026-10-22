"""ingest_dataroom (Part 0): data/dataroom/{deal}/raw/ -> data/dataroom/{deal}/export/.

For each deal:
  1. number the raw files DR-<deal>-NNN in sorted filename order; give each a tier from
     params ingest.dataroom_tiers (filename keyword -> tier); write export/manifest.csv
     (CS1 columns stem, accession plus deal_id, source_type, file, form, tier, sha256)
  2. run the CS1 stage scripts unchanged, through their path arguments, on the deal's PDFs:
     parse_text.py (text layer + OCR trigger, ocr_log.csv), layout.py (blocks), tables.py (Camelot on
     statement pages, tables_log.csv) and docling_parse.py (second, independent parse; html_stem unset)
  3. build lantern/2.0 records (src/ingest/records.py) and write export/records.jsonl:
       PDF page with a text layer -> CS1 layout blocks
       PDF page OCR'd by parse_text -> its full-page OCR words grouped with CS1 fallback_blocks
         (layout's per-box OCR clipped and dropped text on scanned pages)
       transcript (.txt)          -> one record per paragraph, text_span citation (lines, chars)
       spreadsheet (.xlsx)        -> one Table record per sheet, sheet_cell citation (Sheet!A1:D9)

Usage:  python -m src.ingest.dataroom            (all deals in params deal.active + deal.practice)
        python -m src.ingest.dataroom --root <dir with variant.json and {deal}/raw> --out-root <dir> --deals deal_a
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import copy
from importlib.metadata import version
from pathlib import Path

import openpyxl
import yaml

from src.ingest.records import write_jsonl

SRC = Path(__file__).resolve().parents[1]           # src/, where the CS1 scripts live
SUFFIXES = {".pdf", ".xlsx", ".txt"}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def doc_type(name: str, tiers: dict) -> tuple[str, int]:
    """First keyword of ingest.dataroom_tiers contained in the filename -> (form, tier)."""
    for key, tier in tiers.items():
        if key in name.lower():
            return key, int(tier)
    raise ValueError(f"{name}: no keyword from ingest.dataroom_tiers matches; add one to params.yaml")


def plan(raw: Path, deal: str, tiers: dict) -> list[dict]:
    files = sorted(p for p in raw.iterdir() if p.is_file() and not p.name.startswith("."))
    unknown = [p.name for p in files if p.suffix.lower() not in SUFFIXES]
    if unknown:
        raise ValueError(f"{raw}: unsupported file types {unknown}")
    rows = []
    for i, p in enumerate(files, 1):
        form, tier = doc_type(p.name, tiers)
        rows.append({"stem": p.stem, "accession": f"DR-{deal}-{i:03d}", "deal_id": deal,
                     "source_type": "dataroom", "file": p.name, "form": form, "tier": tier,
                     "sha256": sha256_of(p)})
    return rows


def write_manifest(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def run_cs1(script: str, *args: str) -> None:
    """Run a CS1 stage script unchanged, exactly as its DVC stage does, with our paths."""
    cmd = [sys.executable, str(SRC / script), *args]
    print("[INFO] $", " ".join(cmd[1:]))
    subprocess.run(cmd, check=True)


def run_cs1_module(module: str, *args: str) -> None:
    """Run a CS1 stage as a module from the repo root, as its DVC stage does (python -m).
    Needed for src/docling_parse.py: run by path, src/ would be first on sys.path and Docling's own
    'import docling_parse' (the docling-parse library) would load the CS1 file of the same name."""
    cmd = [sys.executable, "-m", module, *args]
    print("[INFO] $", " ".join(cmd[1:]))
    subprocess.run(cmd, check=True, cwd=SRC.parent)


def base_record(row: dict, deal: str, company: str, extractor: str, extractor_version: str) -> dict:
    return {
        "schema": "lantern/2.0", "doc_id": row["accession"], "company": company, "cik": None,
        "ticker": None, "form": row["form"], "fiscal_year": None, "fiscal_period": None,
        "extractor": extractor, "extractor_version": extractor_version,
        "source_path": f"data/dataroom/{deal}/raw/{row['file']}", "sha256": row["sha256"],
        "deal_id": deal, "source_type": "dataroom", "tier": row["tier"],
        "lineage": {"parents": [], "source_sha256": row["sha256"]}, "ingest_flags": [],
        "table": None, "ocr": False, "ocr_conf": None,
        "detector": None, "detector_score": None, "figure_path": None,
    }

def ocr_lines(words: list[dict], min_overlap: float) -> list[dict]:
    """Group OCR words into lines by vertical overlap, not by top edge. Tesseract boxes for short
    letters (a, ce, on, may) start a few points lower than for tall ones, so CS1's top-edge tolerance
    (layout.fallback_y_tol_pt, tuned for PDF text layers) split OCR lines and its min_chars filter then
    dropped the short pieces (e.g. '60'). Nothing is dropped here."""
    lines = []
    for w in sorted(words, key=lambda w: (w["top"] + w["bottom"]) / 2):
        for ln in lines:
            ov = min(ln["bottom"], w["bottom"]) - max(ln["top"], w["top"])
            if ov >= min_overlap * min(ln["bottom"] - ln["top"], w["bottom"] - w["top"]):
                ln["words"].append(w)
                ln["top"], ln["bottom"] = min(ln["top"], w["top"]), max(ln["bottom"], w["bottom"])
                break
        else:
            lines.append({"top": w["top"], "bottom": w["bottom"], "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w["x0"])
    lines.sort(key=lambda ln: ln["top"])
    return lines


def ocr_blocks(words: list[dict], min_overlap: float, gap_ratio: float) -> list[dict]:
    """Merge consecutive OCR lines into one block when the vertical gap is at most gap_ratio x the
    median line height. Relative to line height, so it works at any scan resolution (on the sample
    contract: wrapped lines are 9 pt apart, paragraphs 24 pt, lines about 12 pt tall)."""
    lines = ocr_lines(words, min_overlap)
    if not lines:
        return []
    heights = sorted(ln["bottom"] - ln["top"] for ln in lines)
    max_gap = gap_ratio * heights[len(heights) // 2]
    blocks = []
    for ln in lines:
        box = [min(w["x0"] for w in ln["words"]), ln["top"], max(w["x1"] for w in ln["words"]), ln["bottom"]]
        if blocks and box[1] - blocks[-1]["bbox"][3] <= max_gap:
            b = blocks[-1]
            b["bbox"] = [min(b["bbox"][0], box[0]), b["bbox"][1], max(b["bbox"][2], box[2]), box[3]]
            b["lines"].append(ln)
        else:
            blocks.append({"bbox": box, "lines": [ln]})
    return blocks

def pdf_records(row, deal, company, work: Path, lpp: dict, ocr: dict, versions: dict) -> list[dict]:
    stem = row["stem"]
    with open(work / "parsed" / "ocr_log.csv", encoding="utf-8") as f:
        ocr_pages = {int(r["page"]) for r in csv.DictReader(f)
                     if r["stem"] == stem and r["triggered"] == "True"}
    words_by_page: dict[int, list[dict]] = {}
    for line in open(work / "parsed" / f"{stem}.words.jsonl", encoding="utf-8"):
        w = json.loads(line)
        if w["page"] in ocr_pages:
            x0, top, x1, bottom = w["bbox"]
            words_by_page.setdefault(w["page"], []).append(
                {"text": w["text"], "x0": x0, "top": top, "x1": x1, "bottom": bottom, "conf": w["conf"]})
    blocks = [json.loads(l) for l in open(work / "layout" / f"{stem}.blocks.jsonl", encoding="utf-8")]
    pages = sorted({b["page"] for b in blocks} | ocr_pages)

    out, section = [], None
    for n in pages:
        if n in ocr_pages:          # scanned page: full-page OCR words, grouped by vertical overlap
            groups = ocr_blocks(words_by_page.get(n, []), ocr["ocr_line_overlap"], ocr["ocr_para_gap_ratio"])
            for i, g in enumerate(groups, 1):
                words = [w for ln in g["lines"] for w in ln["words"]]
                confs = [w["conf"] for w in words if w["conf"] is not None]
                bbox = [round(v, 2) for v in g["bbox"]]
                rec = base_record(row, deal, company, "tesseract", versions["tesseract"])
                rec.update({"page": n, "section": section, "block_id": f"p{n:04d}_b{i:03d}",
                            "block_type": "Text", "bbox": bbox,
                            "text": "\n".join(" ".join(w["text"] for w in ln["words"]) for ln in g["lines"]),
                            "ocr": True, "ocr_conf": round(sum(confs) / len(confs), 1) if confs else None,
                            "detector": "ocr_lines",
                            "citation": {"kind": "pdf_bbox", "doc_id": row["accession"], "page": n, "bbox": bbox}})
                out.append(rec)
        else:                       # text-layer page: CS1 layout blocks as they are
            for b in (b for b in blocks if b["page"] == n):
                section = b["section"] or section
                ocr = bool(b["ocr"])
                rec = base_record(row, deal, company,
                                  "tesseract" if ocr else "pdfplumber",
                                  versions["tesseract"] if ocr else versions["pdfplumber"])
                rec.update({"page": n, "section": b["section"], "block_id": b["block_id"],
                            "block_type": b["type"], "bbox": b["bbox"], "text": b["text"],
                            "table": b["table"] if b["type"] == "Table" else None,
                            "ocr": ocr, "ocr_conf": b["ocr_conf"] if ocr else None,
                            "detector": b["model"] if b["source"] == "model" else b["source"],
                            "detector_score": b["score"], "figure_path": b["figure_path"],
                            "citation": {"kind": "pdf_bbox", "doc_id": row["accession"], "page": n,
                                         "bbox": b["bbox"]}})
                out.append(rec)
    print(f"[INFO] {row['accession']} {row['file']}: {len(out)} records; OCR pages {sorted(ocr_pages) or 'none'} (ocr_log.csv)")
    return out


def txt_records(row, deal, company, path: Path) -> list[dict]:
    """One record per non-empty line (a transcript turn), located by line number and character offsets."""
    text = path.read_text(encoding="utf-8")
    out, pos = [], 0
    for ln_no, line in enumerate(text.splitlines(keepends=True), 1):
        body = line.rstrip("\r\n")
        if body.strip():
            i = len(out) + 1
            rec = base_record(row, deal, company, "python-text", sys.version.split()[0])
            rec.update({"page": None, "section": None, "block_id": f"t{i:03d}", "block_type": "Text",
                        "bbox": None, "text": body.strip(),
                        "citation": {"kind": "text_span", "doc_id": row["accession"],
                                     "line_start": ln_no, "line_end": ln_no,
                                     "char_start": pos, "char_end": pos + len(body)}})
            out.append(rec)
        pos += len(line)
    print(f"[INFO] {row['accession']} {row['file']}: {len(out)} records (text_span)")
    return out


def xlsx_records(row, deal, company, path: Path) -> list[dict]:
    """One Table record per sheet. A formula cell with no stored value raises (Lab 13): data_only
    reading would return None and the number would silently disappear."""
    values = openpyxl.load_workbook(path, data_only=True, read_only=True)
    formulas = openpyxl.load_workbook(path, data_only=False, read_only=True)
    out = []
    for i, ws in enumerate(values.worksheets, 1):
        fws = formulas[ws.title]
        rows = []
        for vrow, frow in zip(ws.iter_rows(), fws.iter_rows()):
            cells = []
            for v, f in zip(vrow, frow):
                if v.value is None and isinstance(f.value, str) and f.value.startswith("="):
                    raise ValueError(f"{path.name} {ws.title}!{f.coordinate}: formula {f.value} has no stored value")
                cells.append(v.value if isinstance(v.value, (int, float)) or v.value is None else str(v.value))
            rows.append(cells)
        while rows and all(c is None for c in rows[-1]):
            rows.pop()
        if not rows:
            continue
        width = max(len(r) for r in rows)
        rows = [r + [None] * (width - len(r)) for r in rows]
        rng = f"{ws.title}!A1:{openpyxl.utils.get_column_letter(width)}{len(rows)}"
        header = ["" if c is None else str(c) for c in rows[0]]
        text = "\n".join(" | ".join("" if c is None else str(c) for c in r) for r in rows)
        rec = base_record(row, deal, company, "openpyxl", version("openpyxl"))
        rec.update({"page": None, "section": ws.title, "block_id": f"s{i:03d}", "block_type": "Table",
                    "bbox": None, "text": text,
                    "table": {"sheet": ws.title, "range": rng, "columns": header, "rows": rows[1:]},
                    "citation": {"kind": "sheet_cell", "doc_id": row["accession"], "cell": rng}})
        out.append(rec)
    print(f"[INFO] {row['accession']} {row['file']}: {len(out)} records (one per sheet)")
    return out


def ingest_deal(deal: str, root: Path, out_root: Path, params: dict, company: str) -> int:
    raw = root / deal / "raw"
    if not raw.is_dir():
        raise FileNotFoundError(f"{raw} not found")
    out = out_root / deal / "export"
    work = out / "work"
    rows = plan(raw, deal, params["ingest"]["dataroom_tiers"])
    write_manifest(rows, out / "manifest.csv")

    if any(r["file"].lower().endswith(".pdf") for r in rows):
        run_cs1("parse_text.py", "--input", str(raw), "--output", str(work / "parsed"),
                "--manifest", str(out / "manifest.csv"))
        run_cs1("layout.py", "--input", str(raw), "--output", str(work / "layout"),
                "--figures", str(work / "figures"), "--manifest", str(out / "manifest.csv"))
        run_cs1("tables.py", "--input", str(raw), "--output", str(work / "tables"),
                "--manifest", str(out / "manifest.csv"))
        # CS1 docling_parse also converts the AAPL filing's HTML named by docling.html_stem and would
        # read a 'ticker' column the data-room manifest does not have; the data room is PDFs only,
        # so pass a params copy with html_stem unset (params.yaml itself is unchanged).
        dl = copy.deepcopy(params)
        dl["docling"]["html_stem"] = None
        dl_params = work / "docling_params.yaml"
        dl_params.write_text(yaml.safe_dump(dl, sort_keys=False), encoding="utf-8")
        run_cs1_module("src.docling_parse", "--input", str(raw), "--output", str(work / "docling"),
                       "--raw", str(raw), "--params", str(dl_params), "--manifest", str(out / "manifest.csv"))

    import pytesseract
    versions = {"pdfplumber": version("pdfplumber"), "tesseract": str(pytesseract.get_tesseract_version())}
    records = []
    for r in rows:
        suffix = Path(r["file"]).suffix.lower()
        if suffix == ".pdf":
            records += pdf_records(r, deal, company, work, params["layout"],
                                   params["ingest"], versions)
        elif suffix == ".txt":
            records += txt_records(r, deal, company, raw / r["file"])
        else:
            records += xlsx_records(r, deal, company, raw / r["file"])
    n = write_jsonl(records, out / "records.jsonl")
    print(f"[INFO] {deal}: {n} validated lantern/2.0 records -> {out / 'records.jsonl'}")
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 0 ingest_dataroom")
    ap.add_argument("--root", default="data/dataroom", help="folder with variant.json and {deal}/raw/")
    ap.add_argument("--out-root", default=None, help="where {deal}/export/ goes (default: --root)")
    ap.add_argument("--deals", nargs="*", default=None, help="default: params deal.active + deal.practice")
    ap.add_argument("--params", default="params.yaml")
    args = ap.parse_args()

    with open(args.params, encoding="utf-8") as f:
        params = yaml.safe_load(f)
    root = Path(args.root).expanduser()
    out_root = Path(args.out_root).expanduser() if args.out_root else root
    deals = args.deals or [*params["deal"]["active"], params["deal"]["practice"]]
    names = json.loads((root / "variant.json").read_text(encoding="utf-8")).get("deals", {})
    for deal in deals:
        ingest_deal(deal, root, out_root, params, names.get(deal, deal))


if __name__ == "__main__":
    main()
