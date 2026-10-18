"""lantern/2.0 records (brief Appendix C), validated on every write.

lantern/2.0 keeps every lantern/1.0 field (CS1 Appendix B) and adds deal_id, source_type, tier,
citation, lineage and ingest_flags. CS1's src/schema.py is left unchanged because the CS1 export
stage still uses it; this model is its 2.0 successor for CS2 ingestion.

EDGAR-only 1.0 fields (cik, ticker, fiscal_year, fiscal_period) are null for data-room and web
records, and a source without pages (transcript, spreadsheet) has page and bbox null.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "lantern/2.0"

BlockType = Literal["Text", "Title", "List", "Table", "Figure", "Footnote"]
DealId = Literal["public", "deal_a", "deal_b", "kestrel"]
SourceType = Literal["edgar_filing", "edgar_xbrl", "edgar_8k_exhibit", "dataroom", "synthetic_web", "derived"]
# Appendix C lists pdf_bbox, ixbrl_element, ixbrl_context, sheet_cell, web_span. text_span (a line and
# character range in a plain-text file) is added for the transcript, as in the Lab 2 kit: none of the
# five kinds can locate a passage in a .txt file. (Open question for the TA.)
CitationKind = Literal["pdf_bbox", "ixbrl_element", "ixbrl_context", "sheet_cell", "web_span", "text_span"]

ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
DR_ID = re.compile(r"^DR-(deal_a|deal_b|kestrel)-\d{3}$")
WEB_ID = re.compile(r"^WEB-[0-9a-f]{8}$")
CIK = re.compile(r"^\d{10}$")
BLOCK_ID = re.compile(r"^(p\d{4}_b\d{3,}|t\d{3,}|s\d{3,})$")   # PDF block, transcript turn, sheet
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EDGAR_TYPES = {"edgar_filing", "edgar_xbrl", "edgar_8k_exhibit"}
NO_PAGES = {"text_span", "sheet_cell", "web_span"}


class Citation(BaseModel):
    model_config = ConfigDict(extra="allow")      # each kind carries its own locator fields
    kind: CitationKind
    doc_id: str


class Lineage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parents: list[str] = []
    source_sha256: str


class RecordV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["lantern/2.0"] = Field(SCHEMA_VERSION, alias="schema")
    # ---- lantern/1.0 fields (CS1 Appendix B) ----
    doc_id: str
    company: Optional[str]
    cik: Optional[str]
    ticker: Optional[str]
    form: Optional[str]
    fiscal_year: Optional[int]
    fiscal_period: Optional[str]
    page: Optional[int] = Field(default=None, ge=1)
    section: Optional[str] = None
    block_id: str
    block_type: BlockType
    bbox: Optional[list[float]] = None
    units: Literal["pt"] = "pt"
    origin: Literal["top-left"] = "top-left"
    text: Optional[str] = None
    table: Optional[dict] = None
    extractor: str
    extractor_version: str
    ocr: bool = False
    ocr_conf: Optional[float] = None
    source_path: str
    sha256: str
    detector: Optional[str] = None
    detector_score: Optional[float] = None
    figure_path: Optional[str] = None
    # ---- added in lantern/2.0 (Appendix C) ----
    deal_id: DealId
    source_type: SourceType
    tier: int = Field(ge=1, le=6)
    citation: Citation
    lineage: Lineage
    ingest_flags: list[str] = []

    @model_validator(mode="after")
    def _rules(self) -> "RecordV2":
        errs = []
        # doc_id format, and deal isolation: a data-room id must name the record's own deal
        if self.source_type == "dataroom":
            if not DR_ID.match(self.doc_id) or self.doc_id.split("-")[1] != self.deal_id:
                errs.append(f"data-room doc_id {self.doc_id!r} must be DR-{self.deal_id}-NNN")
            if self.deal_id == "public":
                errs.append("data-room records cannot have deal_id 'public'")
        elif self.source_type in EDGAR_TYPES:
            if not ACCESSION.match(self.doc_id):
                errs.append(f"EDGAR doc_id must be an accession number, got {self.doc_id!r}")
            if self.deal_id != "public":
                errs.append("EDGAR records must have deal_id 'public'")
            if not (self.cik and CIK.match(self.cik)):
                errs.append("EDGAR records need a 10-digit cik")
        elif self.source_type == "synthetic_web" and not WEB_ID.match(self.doc_id):
            errs.append(f"web doc_id must be WEB-<sha8>, got {self.doc_id!r}")
        if self.source_type != "dataroom" and self.source_type not in EDGAR_TYPES and self.cik:
            errs.append("cik is only for EDGAR records")

        if not BLOCK_ID.match(self.block_id):
            errs.append(f"block_id {self.block_id!r} must look like p0001_b001, t001 or s001")
        if self.citation.doc_id != self.doc_id:
            errs.append("citation.doc_id must equal doc_id")
        if self.lineage.source_sha256 != self.sha256:
            errs.append("lineage.source_sha256 must equal sha256")
        if not SHA256.match(self.sha256):
            errs.append("sha256 must be 64 lowercase hex characters")
        if "\\" in self.source_path or self.source_path.startswith("/"):
            errs.append(f"source_path must be relative with forward slashes, got {self.source_path!r}")

        # location: paged sources need page + bbox; unpaged sources have neither
        if self.citation.kind in NO_PAGES:
            if self.page is not None or self.bbox is not None:
                errs.append(f"{self.citation.kind} records have no page or bbox")
        elif self.citation.kind == "pdf_bbox":
            if self.page is None or self.bbox is None:
                errs.append("pdf_bbox records need page and bbox")
            else:
                x0, top, x1, bottom = self.bbox
                if not (len(self.bbox) == 4 and x0 < x1 and top < bottom and min(self.bbox) >= -1):
                    errs.append(f"bad bbox {self.bbox}")

        # content rules, as in CS1
        if self.ocr_conf is not None and not self.ocr:
            errs.append("ocr_conf is only allowed when ocr is true")
        if self.ocr_conf is not None and not 0 <= self.ocr_conf <= 100:
            errs.append(f"ocr_conf must be 0-100, got {self.ocr_conf}")
        if self.block_type in ("Text", "Title", "List", "Footnote") and self.text is None:
            errs.append(f"{self.block_type} block {self.block_id} needs text")
        if self.block_type != "Table" and self.table is not None:
            errs.append("only Table blocks may carry a table")
        if errs:
            raise ValueError("; ".join(errs))
        return self


def write_jsonl(records: list[dict], path: Path) -> int:
    """Validate every record, then write JSONL (UTF-8, LF). Nothing is written if any record fails."""
    lines = [RecordV2.model_validate(r).model_dump_json(by_alias=True) for r in records]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for line in lines:
            f.write(line + "\n")
    return len(lines)
