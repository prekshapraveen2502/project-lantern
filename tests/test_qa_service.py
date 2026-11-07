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
