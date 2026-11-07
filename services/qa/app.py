"""Part 4 item 1: the grounded answering service.

  POST /answer          body {question, scope}; scope must be one of deal_a, deal_b, kestrel, public (else 422)
  GET  /facts/{id}      a fact (F-...) or a claim (C-...) from data/facts/facts.duckdb, 404 if unknown
  GET  /calc/{calc_id}  a calculator output with its trace from outputs/qa/calcs.jsonl, 404 if unknown
  GET  /health          status, LLM mode, fact and claim counts

Paths come from environment variables (defaults are the repo paths), so the container can mount data elsewhere:
LANTERN_FACTS_DB, LANTERN_CALCS. Run: uvicorn services.qa.app:app
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.calc import core as calc
from src.qa import store

Scope = Literal["deal_a", "deal_b", "kestrel", "public"]


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1)
    scope: Scope


def create_app(facts_db: Path | None = None, calcs: Path | None = None, answer_fn=None) -> FastAPI:
    facts_db = Path(facts_db or os.environ.get("LANTERN_FACTS_DB", "data/facts/facts.duckdb"))
    calcs = Path(calcs or os.environ.get("LANTERN_CALCS", "outputs/qa/calcs.jsonl"))
    app = FastAPI(title="LANTERN grounded answering service")

    @app.get("/health")
    def health():
        mode = os.environ.get("LANTERN_LLM_MODE")
        if mode is None:
            import yaml
            mode = yaml.safe_load(Path("params.yaml").read_text(encoding="utf-8"))["llm"]["mode"]
        return {"status": "ok", "llm_mode": mode, **store.counts(facts_db)}

    @app.get("/facts/{fact_id}")
    def facts(fact_id: str):
        got = store.get(facts_db, fact_id)
        if got is None:
            raise HTTPException(404, f"unknown fact or claim id {fact_id}")
        return got

    @app.get("/calc/{calc_id}")
    def calc_out(calc_id: str):
        got = calc.load(calcs).get(calc_id)
        if got is None:
            raise HTTPException(404, f"unknown calc_id {calc_id}")
        return got

    @app.post("/answer")
    def answer(req: AnswerRequest):
        if answer_fn is None:
            raise HTTPException(501, "the answer model is added in Part 4 item 2")
        return answer_fn(req.question, req.scope)

    return app


app = create_app()
