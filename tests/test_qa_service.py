"""
Part 4 tests: the answering service (services/qa/app.py) and the minimal calculator (src/calc/core.py), on a toy
DuckDB and a temporary calc registry; no real data, no LLM.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.calc import core as calc  # noqa: E402

F_CUR = {"fact_id": "F-cur", "value": 59.3e6, "unit": "USD", "tier": 3}
F_PRI = {"fact_id": "F-pri", "value": 48.7e6, "unit": "USD", "tier": 3}
F_TPV = {"fact_id": "F-tpv", "value": 4.87e9, "unit": "USD", "tier": 1}


@pytest.fixture
def db(tmp_path):
    import duckdb
    p = tmp_path / "facts.duckdb"
    con = duckdb.connect(str(p))
    con.execute("create table facts (fact_id varchar, deal_id varchar, metric varchar, value double, unit varchar, "
                "tier integer, \"end\" date, citation json)")
    con.execute("insert into facts values ('F-1', 'kestrel', 'revenue', 59300000, 'USD', 3, '2025-06-30', "
                "'{\"kind\": \"sheet_cell\", \"cell\": \"P&L!D6\"}')")
    con.execute("create table claims (claim_id varchar, deal_id varchar, metric varchar, value double, tier integer, "
                "bbox double[])")
    con.execute("insert into claims values ('C-1', 'kestrel', 'headcount', 410, 4, [1.0, 2.0, 3.0, 4.0])")
    con.close()
    return p


@pytest.fixture
def client(db, tmp_path):
    from fastapi.testclient import TestClient
    from services.qa.app import create_app
    reg = tmp_path / "calcs.jsonl"
    calc.register([calc.growth(F_CUR, F_PRI)], reg)
    return TestClient(create_app(facts_db=db, calcs=reg))


def test_growth_ratio_difference_with_trace_and_lowest_tier():
    g = calc.growth(F_CUR, F_PRI)
    assert g["value"] == pytest.approx(59.3 / 48.7 - 1) and g["unit"] == "pure"
    assert [t["fact_id"] for t in g["trace"]] == ["F-cur", "F-pri"] and g["calc_id"].startswith("CALC-")
    r = calc.ratio(F_CUR, F_TPV)
    assert r["value"] == pytest.approx(59.3e6 / 4.87e9) and r["tier"] == 3          # lowest tier of 3 and 1
    d = calc.difference(F_CUR, F_PRI)
    assert d["value"] == pytest.approx(10.6e6) and d["unit"] == "USD"


def test_calc_id_is_deterministic_and_depends_on_op_and_input_order():
    assert calc.growth(F_CUR, F_PRI)["calc_id"] == calc.growth(dict(F_CUR), dict(F_PRI))["calc_id"]
    assert calc.growth(F_CUR, F_PRI)["calc_id"] != calc.growth(F_PRI, F_CUR)["calc_id"]
    assert calc.growth(F_CUR, F_PRI)["calc_id"] != calc.ratio(F_CUR, F_PRI)["calc_id"]


def test_calculator_refuses_bad_inputs():
    with pytest.raises(calc.CalcError):
        calc.ratio(F_CUR, {**F_PRI, "value": 0})
    with pytest.raises(calc.CalcError):
        calc.difference(F_CUR, {**F_PRI, "unit": "pure"})
    with pytest.raises(calc.CalcError):
        calc.growth(F_CUR, {**F_PRI, "value": None})


def test_registry_appends_each_calc_once(tmp_path):
    reg = tmp_path / "calcs.jsonl"
    g = calc.growth(F_CUR, F_PRI)
    assert calc.register([g, g], reg) == 1 and calc.register([g], reg) == 0
    assert list(calc.load(reg)) == [g["calc_id"]] and len(reg.read_text().splitlines()) == 1


def test_health(client):
    got = client.get("/health").json()
    assert got["status"] == "ok" and got["facts"] == 1 and got["claims"] == 1


def test_facts_endpoint_serves_facts_and_claims_and_404s(client):
    f = client.get("/facts/F-1").json()
    assert f["kind"] == "fact" and f["value"] == 59300000 and f["citation"]["cell"] == "P&L!D6"
    assert f["end"] == "2025-06-30"
    c = client.get("/facts/C-1").json()
    assert c["kind"] == "claim" and c["tier"] == 4 and c["bbox"] == [1.0, 2.0, 3.0, 4.0]
    assert client.get("/facts/F-nope").status_code == 404


def test_calc_endpoint_serves_the_registry(client):
    g = calc.growth(F_CUR, F_PRI)
    got = client.get(f"/calc/{g['calc_id']}").json()
    assert got["value"] == pytest.approx(g["value"]) and got["trace"][0]["fact_id"] == "F-cur"
    assert client.get("/calc/CALC-000000000000").status_code == 404


@pytest.mark.parametrize("scope", ["deal_c", "", "ALL", None])
def test_answer_rejects_unknown_scopes(client, scope):
    assert client.post("/answer", json={"question": "revenue?", "scope": scope}).status_code == 422


def test_answer_needs_a_question(client):
    assert client.post("/answer", json={"question": "", "scope": "kestrel"}).status_code == 422


def test_answer_calls_the_pipeline_and_a_replay_cache_miss_is_503(db, tmp_path):
    from fastapi.testclient import TestClient
    from services.qa.app import create_app
    ok = TestClient(create_app(facts_db=db, calcs=tmp_path / "c.jsonl",
                               answer_fn=lambda q, s: {"status": "answered", "question": q, "scope": s}))
    assert ok.post("/answer", json={"question": "revenue?", "scope": "kestrel"}).json()["scope"] == "kestrel"

    def miss(q, s):
        raise RuntimeError("cache miss abc123")
    got = TestClient(create_app(facts_db=db, calcs=tmp_path / "c.jsonl", answer_fn=miss)).post(
        "/answer", json={"question": "revenue?", "scope": "kestrel"})
    assert got.status_code == 503 and "not in replay cache" in got.json()["detail"]


# ---- run_dev acceptance checks (src/qa/run_dev.py) on a toy store ----

@pytest.fixture
def check_db(tmp_path):
    import duckdb
    p = tmp_path / "f.duckdb"
    con = duckdb.connect(str(p))
    con.execute("create table facts (fact_id varchar, doc_id varchar, value double, scale integer, tier integer, "
                "citation json)")
    con.execute("insert into facts values ('F-rev', 'DR-k-005', 59300000, 6, 3, "
                "'{\"kind\": \"sheet_cell\", \"block_id\": \"s001\", \"cell\": \"P&L!D6\"}')")
    con.execute("create table claims (claim_id varchar, doc_id varchar, block_id varchar, value double, scale integer, "
                "tier integer)")
    con.execute("insert into claims values ('C-g', 'DR-k-001', 'p3', 96400000, 6, 4)")
    con.close()
    return p


def answer_with(text, refs, cites):
    return {"status": "answered", "rendered": text, "claims": [{"text": text, "numbers": [
        {"value_text": v, "ref_type": t, "ref": r} for v, t, r in refs], "citations": cites}]}


def test_check_answer_relints_against_the_store_and_finds_numbers_in_their_blocks(check_db):
    from src.qa.run_dev import Blocks, check_answer
    blocks = Blocks({("DR-k-005", "s001"): {"text": "USD millions | FY2025\nNet revenue | 59.3"},
                     ("DR-k-001", "p3"): {"text": "FY2025 revenue of $96.4m"}}, Path("/nonexistent"))
    cite = lambda d, b, t: {"doc_id": d, "block_id": b, "page": None, "bbox": None, "tier": t}
    ok = check_answer(answer_with("Net revenue was $59.3 million.", [("$59.3 million", "fact_id", "F-rev")],
                                  [cite("DR-k-005", "s001", 3)]), check_db, {}, blocks, 0.005)
    assert (ok["numbers"], ok["numbers_resolved"], ok["cited_numbers_contained"], ok["citations_resolved"]) == (1, 1, 1, 1)
    claim = check_answer(answer_with("The CIM says $96.4 million.", [("$96.4 million", "claim_id", "C-g")],
                                     [cite("DR-k-001", "p3", 4)]), check_db, {}, blocks, 0.005)
    assert claim["cited_numbers_contained"] == 1 and not claim["citation_problems"]


def test_check_answer_reports_unresolved_numbers_and_missing_blocks(check_db):
    from src.qa.run_dev import Blocks, check_answer
    blocks = Blocks({("DR-k-005", "s001"): {"text": "Net revenue | 48.7"}}, Path("/nonexistent"))
    cite = {"doc_id": "DR-k-009", "block_id": "x", "page": None, "bbox": None, "tier": 3}
    got = check_answer(answer_with("Net revenue was $59.0 million, 3 times.", [("$59.0 million", "fact_id", "F-rev")],
                                   [cite]), check_db, {}, blocks, 0.005)
    assert got["numbers"] == 2 and got["numbers_resolved"] == 0 and len(got["unresolved"]) == 2
    assert got["citations_resolved"] == 0 and got["cited_numbers_contained"] == 0
    assert any("does not resolve" in p for p in got["citation_problems"])
    assert any("not shown in DR-k-005:s001" in p for p in got["citation_problems"])


def test_a_cost_stored_positive_is_found_as_shown_in_its_block(tmp_path):
    from src.qa.run_dev import Blocks, contains
    blocks = Blocks({("DR-k-005", "s001"): {"text": "USD millions\nPass-through costs | -30.2 | -37.1"}}, Path("/x"))
    item = {"kind": "fact", "doc_id": "DR-k-005", "value": 37.1e6, "scale": 6,
            "citation": {"kind": "sheet_cell", "block_id": "s001"}, "lineage": {"sign_flipped": True}}
    assert contains(blocks, item, 0.005)[0]
    assert not contains(blocks, {**item, "lineage": {}}, 0.005)[0]
