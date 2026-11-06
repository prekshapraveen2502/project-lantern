"""Part 1 item 4: seller financials (data-room xlsx) -> tier-3 facts with sheet_cell citations.

Rules are the DECISIONS.md lines of 2026-11-05 (item 4 Q1-Q5); anything they do not cover stops the run (XlsxError):
  sheets     hidden sheets are skipped and logged (name, state, row count)
  FYE        from "fiscal year end(s|ed) <Month> <day>" statements; disagreeing statements stop; FY columns without
             a statement stop. An explicit range ("FY2025 = July 1, 2024 to June 30, 2025") must match the derived
             period, otherwise lineage.fy_label_convention = end_year is recorded
  periods    a header row of ^FY\\d{4}$ cells (after normalisation) maps columns to fiscal years; any other label in
             it stops. A sheet with one value column and no header takes FY\\d{4} from its title. A heading row with
             no values that names an FY is a check on the values below it
  scale      currency: a scale word in the row label wins; otherwise a sheet-level unit (a cell above the data that is
             only a unit, or a unit in parentheses in the title); otherwise stop. percent/count: scale 0; a percent
             metric whose cell is not %-formatted stops; a ratio row stops
  values     text cells ("n/a") give no fact; sign: cost metrics are stored positive (lineage.sign_flipped)
  unmapped   rows matching no metric are stored with metric NULL when the sheet's mapped rows share one period type;
             on a mixed sheet or a sheet with no mapped rows they are logged, not stored
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path

import openpyxl

from src.facts.periods import fiscal_labels, fye_date
from src.facts.vocab import match_line, normalise_label

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
FYE_RE = re.compile(r"fiscal year end(?:s|ed)?\s+(" + "|".join(MONTHS) + r")\s+(\d{1,2})\b", re.I)
RANGE_RE = re.compile(r"\bFY(\d{4})\s*=\s*([A-Za-z]+ \d{1,2}, \d{4})\s+to\s+([A-Za-z]+ \d{1,2}, \d{4})", re.I)
FY_HEADER = re.compile(r"^fy(\d{4})$")
FY_IN_TEXT = re.compile(r"\bfy(\d{4})\b", re.I)


class XlsxError(ValueError):
    """A case the item 4 rules do not cover: the run stops."""


def _date(s: str) -> date:
    m, d, y = re.match(r"([A-Za-z]+) (\d{1,2}), (\d{4})", s).groups()
    return date(int(y), MONTHS[m.lower()], int(d))


def find_fye(texts: list[tuple[str, str]]) -> tuple[str | None, list[str]]:
    """(MMDD or None, cells that state it). Two statements that disagree stop the run."""
    found = {}
    for cell, text in texts:
        for month, day in FYE_RE.findall(text):
            found.setdefault(f"{MONTHS[month.lower()]:02d}{int(day):02d}", []).append(cell)
    if len(found) > 1:
        raise XlsxError(f"fiscal year end statements disagree: {found}")
    return next(iter(found.items()), (None, []))


def check_fy_ranges(texts: list[tuple[str, str]], fye: str) -> list[str]:
    """Cells stating an explicit FY range; each must equal the period derived from the fiscal year end."""
    cells = []
    for cell, text in texts:
        for fy, a, b in RANGE_RE.findall(text):
            fy = int(fy)
            derived = (fye_date(fy - 1, fye) + timedelta(days=1), fye_date(fy, fye))
            if (_date(a), _date(b)) != derived:
                raise XlsxError(f"{cell}: FY{fy} stated as {a} to {b}, derived {derived[0]} to {derived[1]}")
            cells.append(cell)
    return cells


def scale_word(text: str, phrases: dict) -> int | None:
    hits = {phrases[w] for w in re.findall(r"[a-z]+", text.lower()) if w in phrases}
    if len(hits) > 1:
        raise XlsxError(f"{text!r} names two scales")
    return hits.pop() if hits else None


def sheet_unit(cells: list[tuple[str, str]], title: str | None, phrases: dict) -> tuple[int | None, str | None]:
    """A cell above the data whose whole normalised text is a unit ("usd millions"), or "(usd millions)" in the title."""
    words = "|".join(map(re.escape, phrases))
    whole = re.compile(rf"^(usd\s+)?({words})$")
    for cell, text in cells:
        m = whole.match(" ".join(str(text).lower().split()))
        if m:
            return phrases[m.group(2)], cell
    if title:
        m = re.search(rf"\((usd\s+)?({words})\)", title[1].lower())
        if m:
            return phrases[m.group(2)], title[0]
    return None, None


def decimals_from_format(fmt: str, scale: int) -> int | None:
    """Precision in base units from the number format: #,##0.0 at scale 6 -> -5; 0.0% -> 3; General -> None."""
    first = (fmt or "").split(";")[0]
    if "0" not in first:
        return None
    m = re.search(r"\.(0+)", first)
    places = len(m.group(1)) if m else 0
    return places + 2 if "%" in first else places - scale


def read_workbook(path: Path, doc_id: str, deal: str, vocab: dict, tol: int, block_ids: dict[str, str],
                  source_sha: str, extractor: str) -> tuple[list[dict], dict]:
    """(fact rows for the facts table, log). Raises XlsxError on any case the rules do not cover."""
    phrases = vocab["scale_phrases"]
    wb = openpyxl.load_workbook(path, data_only=True)
    log = {"hidden_sheets_skipped": [], "unmapped_stored": [], "unmapped_not_stored": [], "text_cells": [],
           "sign_flipped": [], "facts": 0}
    texts = [(f"{ws.title}!{c.coordinate}", c.value) for ws in wb.worksheets if ws.sheet_state == "visible"
             for row in ws.iter_rows() for c in row if isinstance(c.value, str)]
    fye, fye_cells = find_fye(texts)
    range_cells = check_fy_ranges(texts, fye) if fye else []
    log.update({"fiscal_year_end": fye, "fye_cells": fye_cells, "fy_range_cells": range_cells})
    rows_out = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            log["hidden_sheets_skipped"].append({"sheet": ws.title, "state": ws.sheet_state, "rows": ws.max_row})
            continue
        rows_out += _read_sheet(ws, doc_id, deal, vocab, tol, block_ids, source_sha, extractor, phrases, fye,
                                fye_cells, range_cells, log, Path(path).as_posix())
    log["facts"] = len(rows_out)
    return rows_out, log


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _read_sheet(ws, doc_id, deal, vocab, tol, block_ids, source_sha, extractor, phrases, fye, fye_cells,
                range_cells, log, source_path) -> list[dict]:
    grid = [list(r) for r in ws.iter_rows()]
    has_numbers = any(_is_num(c.value) for r in grid for c in r[1:])
    if not has_numbers:
        return []                                         # text-only sheet (e.g. Notes)
    rules = vocab["normalise"]
    # 1. period columns: a header row of FY labels, or a single value column dated by the title
    header_idx, col_fy = None, {}
    for i, r in enumerate(grid):
        fys = {j: int(FY_HEADER.match(normalise_label(c.value, rules)).group(1)) for j, c in enumerate(r)
               if isinstance(c.value, str) and FY_HEADER.match(normalise_label(c.value, rules))}
        if fys:
            others = [c.coordinate for j, c in enumerate(r) if j > 0 and c.value is not None and j not in fys]
            if others:
                raise XlsxError(f"{ws.title}: header row {i + 1} has labels other than FYyyyy at {others}")
            header_idx, col_fy = i, fys
            break
    title = next(((f"{ws.title}!{c.coordinate}", c.value) for r in grid for c in r[:1] if isinstance(c.value, str)),
                 None)
    if header_idx is None:
        value_cols = {j for r in grid for j, c in enumerate(r) if j > 0 and _is_num(c.value)}
        m = FY_IN_TEXT.search(title[1]) if title else None
        if len(value_cols) != 1 or not m:
            raise XlsxError(f"{ws.title}: no FYyyyy header row and not a single value column dated by its title")
        col_fy = {value_cols.pop(): int(m.group(1))}
        first_data = next(i for i, r in enumerate(grid) if any(_is_num(c.value) for c in r[1:]))
        above = grid[1:first_data]
    else:
        above = grid[:header_idx]
    if fye is None:
        raise XlsxError(f"{ws.title}: FY columns but no fiscal year end statement in the workbook")
    unit_cells = [(f"{ws.title}!{c.coordinate}", c.value) for r in above for c in r if isinstance(c.value, str)]
    sheet_scale, sheet_scale_cell = sheet_unit(unit_cells, title, phrases)
    # 2. data rows and heading checks
    start = (header_idx + 1) if header_idx is not None else 1
    candidates, heading_fy = [], None
    for r in grid[start:]:
        label = r[0].value if r else None
        cells = [c for j, c in enumerate(r) if j in col_fy and c.value is not None]
        stray = [c.coordinate for j, c in enumerate(r) if j > 0 and j not in col_fy and c.value is not None]
        if stray:
            raise XlsxError(f"{ws.title}: values outside the FY columns at {stray}")
        if label is None and not cells:
            heading_fy = None                                  # blank row ends a sub-table
            continue
        if not cells:                                          # heading row: a check on the rows below it
            m = FY_IN_TEXT.search(str(label))
            heading_fy = int(m.group(1)) if m else None
            continue
        if label is None:
            raise XlsxError(f"{ws.title}!{r[0].coordinate}: values without a row label")
        for c in cells:
            fy = col_fy[c.column - 1]
            if heading_fy is not None and fy != heading_fy:
                raise XlsxError(f"{ws.title}!{c.coordinate}: under a heading naming FY{heading_fy} but in an FY{fy} column")
        candidates.append((str(label), match_line(label, vocab), cells))
    mapped_periods = {vocab["metrics"][m]["period"] for _, m, _ in candidates if m}
    sheet_period = mapped_periods.pop() if len(mapped_periods) == 1 else None
    out = []
    for label, metric, cells in candidates:
        m = vocab["metrics"].get(metric) if metric else None
        if m is None and sheet_period is None:
            reason = "sheet mixes instant and duration rows" if mapped_periods else "sheet has no mapped rows"
            for c in cells:
                log["unmapped_not_stored"].append({"cell": f"{ws.title}!{c.coordinate}", "label": label, "reason": reason})
            continue
        for c in cells:
            where = f"{ws.title}!{c.coordinate}"
            if not _is_num(c.value):
                log["text_cells"].append({"cell": where, "label": label, "text": str(c.value)})
                continue
            unit_type = m["unit_type"] if m else ("percent" if "%" in (c.number_format or "") else "currency")
            if unit_type == "ratio":
                raise XlsxError(f"{where}: ratio rows are not decided")
            if unit_type == "percent":
                if "%" not in (c.number_format or ""):
                    raise XlsxError(f"{where}: percent metric {metric} but the cell is not %-formatted")
                unit, scale, scale_source = "pure", 0, {"kind": "percent_format", "cell": where}
            elif unit_type == "count":
                unit, scale, scale_source = "count", 0, {"kind": "count", "cell": where}
            else:
                row_scale = scale_word(normalise_label(label, vocab["normalise"]), phrases)
                if row_scale is not None:
                    scale, scale_source = row_scale, {"kind": "row_label", "cell": f"{ws.title}!A{c.row}"}
                elif sheet_scale is not None:
                    scale, scale_source = sheet_scale, {"kind": "sheet_header", "cell": sheet_scale_cell}
                else:
                    raise XlsxError(f"{where}: currency row {label!r} with no stated scale")
                unit = "USD"
            period_type = m["period"] if m else sheet_period
            fy = col_fy[c.column - 1]
            end = fye_date(fy, fye)
            start = None if period_type == "instant" else fye_date(fy - 1, fye) + timedelta(days=1)
            fiscal_year, fiscal_period = fiscal_labels(start, end, fye, tol)
            value = float(c.value) * 10 ** scale
            lineage = {"parents": [], "source_sha256": source_sha, "label": label, "scale_source": scale_source,
                       "fye_cells": fye_cells}
            lineage.update({"fy_range_cells": range_cells} if range_cells else {"fy_label_convention": "end_year"})
            if m and m.get("sign") == "cost" and value < 0:
                value = -value
                lineage.update({"sign_flipped": True, "value_as_shown": c.value})
                log["sign_flipped"].append({"cell": where, "label": label, "value_as_shown": c.value})
            if ws.title not in block_ids:
                raise XlsxError(f"{ws.title}: no Part 0 record (block_id) for this sheet")
            cell_ref = f"{ws.title}!{c.coordinate}"
            if m is None:
                log["unmapped_stored"].append({"cell": cell_ref, "label": label, "unit": unit, "scale": scale,
                                               "period_type": period_type})
            out.append({
                "fact_id": "F-" + hashlib.sha256(f"{doc_id}|{cell_ref}".encode()).hexdigest()[:12],
                "deal_id": deal, "entity_id": deal, "concept": None, "metric": metric, "value": value, "unit": unit,
                "scale": scale, "period_type": period_type, "start": str(start) if start else None, "end": str(end),
                "fiscal_year": fiscal_year, "fiscal_period": fiscal_period, "dims": "{}",
                "decimals": decimals_from_format(c.number_format, scale), "tier": 3, "source_type": "dataroom",
                "doc_id": doc_id, "filed": None,
                "citation": json.dumps({"kind": "sheet_cell", "doc_id": doc_id, "cell": cell_ref,
                                        "block_id": block_ids[ws.title], "source_path": source_path},
                                       sort_keys=True),
                "extractor": extractor, "lineage": json.dumps(lineage, sort_keys=True, default=str),
            })
    return out
