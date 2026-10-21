"""Base de conocimiento: visibilidad, versionado, prioridad, vencimiento, catálogo y exportación OKF."""

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

from agent_dashboards import config
from agent_dashboards.knowledge import okf_export, service as service_mod, tools
from agent_dashboards.knowledge.catalog_sync import flatten_fields, table_from_info
from agent_dashboards.knowledge.service import KnowledgeError, KnowledgeService
from agent_dashboards.knowledge.store import KnowledgeStore, fts_query
from agent_dashboards.warehouse.mcp_client import WarehouseError

TABLE = "p.ds.ventas"

TABLE_INFO = {
    "id": "p:ds.ventas",
    "type": "TABLE",
    "description": "Ventas diarias por suscripción",
    "numRows": "1200",
    "numBytes": "4096",
    "lastModifiedTime": "1792010209605",
    "timePartitioning": {"type": "DAY", "field": "fecha"},
    "clustering": {"fields": ["pais"]},
    "schema": {
        "fields": [
            {"name": "fecha", "type": "DATE"},
            {"name": "status", "type": "STRING", "description": "Estado de la suscripción"},
            {"name": "pais", "type": "STRING"},
            {"name": "precio", "type": "RECORD", "fields": [{"name": "monto", "type": "NUMERIC"}]},
        ]
    },
}


class FakeWarehouse:
    def __init__(self, fail_dry_run: bool = False):
        self.fail_dry_run = fail_dry_run
        self.dry_runs: list[str] = []
        self.tables = {"ds": {"ventas": TABLE_INFO}}

    async def dry_run(self, sql):
        self.dry_runs.append(sql)
        if self.fail_dry_run:
            raise WarehouseError("Unrecognized name: colx")
        return 1024

    async def call_tool(self, name, args):
        tables = self.tables.get(args["datasetId"], {})
        if name == "list_table_ids":
            return {"tables": [{"id": f"p:{args['datasetId']}.{t}", "type": "TABLE"} for t in tables]}
        if name == "get_table_info":
            return tables[args["tableId"]]
        raise AssertionError(name)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wh():
    return FakeWarehouse()


@pytest.fixture
def svc(tmp_path, monkeypatch, wh):
    monkeypatch.setattr(config, "WAREHOUSE_DATASETS", ["p.ds"])
    monkeypatch.setattr(config, "KNOWLEDGE_CURATORS", {"lucia"})
    s = KnowledgeService(KnowledgeStore(tmp_path / "k.db"), warehouse=wh, okf_dir=tmp_path / "okf")
    run(s.sync_catalog())
    return s


def draft(**kw):
    base = {"target_type": "table", "target_ref": TABLE, "kind": "descripcion", "content": "Una fila por venta.", "visibility": "shared"}
    return {**base, **kw}


def propose(svc, user="ana", **kw):
    return run(svc.propose(draft(**kw), user))["entry"]


# --- catálogo --------------------------------------------------------------------


def test_catalog_sync_reads_bigquery_metadata(svc):
    [t] = svc.store.catalog(TABLE)
    assert t["description"] == "Ventas diarias por suscripción"
    assert t["num_rows"] == 1200 and t["partition_field"] == "fecha" and t["clustering"] == ["pais"]
    assert [c["name"] for c in t["columns"]] == ["fecha", "status", "pais", "precio", "precio.monto"]
    assert t["last_modified"].endswith("Z")


def test_catalog_sync_removes_dropped_tables_but_keeps_failed_ones(svc, wh):
    wh.tables["ds"]["otra"] = {**TABLE_INFO, "id": "p:ds.otra"}
    run(svc.sync_catalog())
    assert svc.store.catalog("p.ds.otra")
    original = wh.call_tool

    async def flaky(name, args):
        if name == "get_table_info" and args["tableId"] == "otra":
            raise WarehouseError("timeout")
        return await original(name, args)

    wh.call_tool = flaky
    result = run(svc.sync_catalog())
    assert not result["ok"] and svc.store.catalog("p.ds.otra")  # falló al leerse: se conserva
    wh.call_tool = original
    del wh.tables["ds"]["otra"]
    run(svc.sync_catalog())
    assert not svc.store.catalog("p.ds.otra")  # ya no existe: se va


def test_table_from_info_handles_views_and_nested_fields():
    t = table_from_info({"id": "p:ds.v", "view": {"query": "SELECT 1"}, "schema": {"fields": []}}, "p.ds", "v")
    assert t["ref"] == "p.ds.v" and t["table_type"] == "VIEW" and t["num_rows"] is None
    cols = flatten_fields([{"name": "a", "type": "RECORD", "mode": "REPEATED", "fields": [{"name": "b", "type": "INT64"}]}])
    assert cols == [{"name": "a", "type": "RECORD", "mode": "REPEATED"}, {"name": "a.b", "type": "INT64", "mode": "NULLABLE"}]


# --- visibilidad ---------------------------------------------------------------------


def test_private_notes_are_invisible_to_others(svc):
    note = propose(svc, user="ana", visibility="private", content="Ojo con junio.")
    assert note["status"] == "approved"  # lo privado no necesita aprobación
    assert [e["id"] for e in svc.lookup(TABLE, "ana")["entries"]] == [note["id"]]
    assert svc.lookup(TABLE, "beto")["entries"] == []
    assert svc.search("junio", "beto")["entries"] == []
    assert svc.list("lucia") == []  # ni siquiera un curador
    with pytest.raises(KnowledgeError):
        svc.history(note["id"], "beto")
    assert "nota privada tuya" in svc.lookup(TABLE, "ana")["entries"][0]["provenance"]


def test_shared_needs_curator_approval(svc):
    e = propose(svc, user="ana")
    assert e["status"] == "proposed"
    seen = svc.lookup(TABLE, "beto")["entries"]
    assert seen[0]["provenance"].startswith("[propuesto por ana, sin aprobar")
    with pytest.raises(KnowledgeError) as err:
        run(svc.review(e["id"], "beto", True))
    assert err.value.code == "forbidden"
    approved = run(svc.review(e["id"], "lucia", True))["entry"]
    assert approved["status"] == "approved" and approved["reviewed_by"] == "lucia"
    assert "aprobado por lucia" in svc.lookup(TABLE, "beto")["entries"][0]["provenance"]
    with pytest.raises(KnowledgeError):
        run(svc.review(e["id"], "lucia", True))  # ya no está pendiente


def test_rejected_entries_leave_reads_but_author_keeps_them(svc):
    e = propose(svc, user="ana")
    run(svc.review(e["id"], "lucia", False, "No es así"))
    assert svc.lookup(TABLE, "beto")["entries"] == []
    mine = svc.list("ana", mine=True, include_inactive=True)
    assert mine[0]["status"] == "rejected" and mine[0]["review_note"] == "No es así"


# --- versionado -----------------------------------------------------------------------------


def test_correction_supersedes_on_approval_and_keeps_history(svc):
    old = propose(svc, user="ana", content="status 'Caído' es baja técnica.")
    run(svc.review(old["id"], "lucia", True))
    fix = propose(svc, user="beto", content="status 'Caído' es churn.", supersedes_id=old["id"])
    # Mientras la corrección no se aprueba, la original sigue vigente.
    assert svc.store.get(old["id"])["status"] == "approved"
    run(svc.review(fix["id"], "lucia", True))
    assert svc.store.get(old["id"])["status"] == "deprecated"
    assert [e["content"] for e in svc.lookup(TABLE, "ana")["entries"]] == ["status 'Caído' es churn."]
    hist = svc.history(fix["id"], "ana")
    assert [e["id"] for e in hist["entries"]] == [old["id"], fix["id"]]
    assert "superseded" in [ev["action"] for ev in hist["events"]]
    # El contenido de la original no cambió: solo su estado.
    assert svc.store.get(old["id"])["content"] == "status 'Caído' es baja técnica."


def test_private_correction_applies_at_once_and_only_by_author(svc):
    note = propose(svc, user="ana", visibility="private", content="v1")
    with pytest.raises(KnowledgeError):
        propose(svc, user="beto", visibility="private", content="v2", supersedes_id=note["id"])
    with pytest.raises(KnowledgeError):  # una corrección mantiene la visibilidad
        propose(svc, user="ana", visibility="shared", content="v2", supersedes_id=note["id"])
    v2 = propose(svc, user="ana", visibility="private", content="v2", supersedes_id=note["id"])
    assert svc.store.get(note["id"])["status"] == "deprecated"
    assert [e["id"] for e in svc.lookup(TABLE, "ana")["entries"]] == [v2["id"]]


def test_deprecate_permissions(svc):
    shared = propose(svc, user="ana")
    run(svc.review(shared["id"], "lucia", True))
    with pytest.raises(KnowledgeError):
        svc.deprecate(shared["id"], "ana")  # lo compartido lo depreca un curador
    svc.deprecate(shared["id"], "lucia", "Ya no aplica")
    assert svc.lookup(TABLE, "ana")["entries"] == []
    note = propose(svc, user="ana", visibility="private", content="mía")
    svc.deprecate(note["id"], "ana")
    assert svc.store.get(note["id"])["status"] == "deprecated"


# --- prioridad y lectura --------------------------------------------------------------------


def test_priority_order(svc):
    proposed = propose(svc, user="beto", content="propuesta")
    private = propose(svc, user="ana", visibility="private", content="nota de ana")
    shared = propose(svc, user="beto", content="compartida")
    run(svc.review(shared["id"], "lucia", True))
    got = svc.lookup(TABLE, "ana")["entries"]
    assert [e["id"] for e in got] == [shared["id"], private["id"], proposed["id"]]
    agent = svc.lookup_for_agent(TABLE, "ana")
    assert agent["catalogo"]["procedencia"].startswith("[catálogo de BigQuery")
    assert "compartido aprobado > notas privadas tuyas > catálogo > propuestas sin aprobar" == agent["orden_de_prioridad"]


def test_lookup_by_short_name_includes_columns(svc):
    col = propose(svc, user="ana", target_type="column", target_ref="ventas.status", content="'Caído' es churn.")
    assert col["target_ref"] == f"{TABLE}.status"  # se completa con el catálogo
    found = svc.lookup("ventas", "ana")
    assert found["target"] == TABLE and [e["id"] for e in found["entries"]] == [col["id"]]


def test_search_matches_words_and_identifiers(svc):
    e = propose(svc, user="ana", kind="regla_filtro", content="Siempre excluir el tráfico de bots.")
    assert [x["id"] for x in svc.search("trafico BOTS", "ana")["entries"]] == [e["id"]]
    assert svc.search("ventas", "ana")["catalog"][0]["ref"] == TABLE
    assert fts_query("UK_SALES_DAILY") == '"UK" OR "SALES"* OR "DAILY"*'
    assert fts_query("  ") is None


def test_index_text_lists_documented_targets(svc):
    propose(svc, user="ana", visibility="private", content="nota")
    run(svc.propose(draft(target_type="metric", target_ref="churn", kind="definicion_metrica",
                          content="Bajas / activos.", sql=f"SELECT 1 FROM `{TABLE}`"), "ana"))
    text = svc.index_text("ana")
    assert "Catálogo de BigQuery: 1 tablas" in text
    assert f"- `{TABLE}` (tabla): 1 nota tuya" in text
    assert "- `churn` (métrica): 1 sin aprobar" in text
    assert "nota tuya" not in svc.index_text("beto")


# --- validación ----------------------------------------------------------------------------


def test_validation_rules(svc, wh):
    bad = [
        dict(kind="nada"),
        dict(target_type="vista"),
        dict(content="  "),
        dict(kind="definicion_metrica"),  # sin SQL
        dict(sql=f"DELETE FROM `{TABLE}` WHERE TRUE"),
        dict(sql="SELECT 1 FROM `otro.ds.t`"),  # dataset no habilitado
        dict(sql=f"SELECT 1 FROM `{TABLE}` WHERE fecha = {{{{desde}}}}"),
        dict(stale_after="2020-01-01"),
        dict(target_type="column", target_ref=f"{TABLE}.no_existe"),
    ]
    for kw in bad:
        with pytest.raises(KnowledgeError):
            propose(svc, **kw)
    propose(svc, content="Una fila por venta.")
    with pytest.raises(KnowledgeError, match="Ya existe"):
        propose(svc, content="una fila por venta.")


def test_sql_is_dry_run_on_approval(svc, wh):
    e = propose(svc, user="ana", kind="consulta_ejemplo", content="Ventas por día", sql=f"SELECT fecha FROM `{TABLE}`")
    assert wh.dry_runs == []  # al proponer solo las guardas
    wh.fail_dry_run = True
    with pytest.raises(KnowledgeError, match="dry run"):
        run(svc.review(e["id"], "lucia", True))
    assert svc.store.get(e["id"])["status"] == "proposed"
    wh.fail_dry_run = False
    run(svc.review(e["id"], "lucia", True))
    assert len(wh.dry_runs) == 2


def test_stale_entries_are_flagged(svc, monkeypatch):
    e = propose(svc, user="ana", visibility="private", kind="advertencia", content="Junio duplicado.", stale_after="2099-01-01")
    assert not svc.lookup(TABLE, "ana")["entries"][0]["stale"]
    monkeypatch.setattr(service_mod, "today", lambda: dt.date(2099, 1, 2))
    got = svc.lookup(TABLE, "ana")["entries"][0]
    assert got["id"] == e["id"] and got["stale"] and "vencida desde 2099-01-01" in got["provenance"]
    assert "vencida" in svc.lookup_for_agent(TABLE, "ana")["conocimiento"][0]


# --- herramienta del agente: confirmación en otro turno ------------------------------------------


def _ctx(state, invocation):
    return SimpleNamespace(user_id="ana", invocation_id=invocation, state=state, session=SimpleNamespace(id="s1"))


def test_propose_tool_needs_confirmation_in_a_later_turn(svc, monkeypatch):
    monkeypatch.setattr(tools, "service", svc)
    state: dict = {}
    first = run(tools.knowledge_propose(_ctx(state, "inv1"), target_type="table", target_ref=TABLE,
                                        kind="advertencia", content="Junio duplicado.", visibility="shared"))
    assert first["ok"] and svc.store.entries() == []  # el borrador no guarda nada
    same_turn = run(tools.knowledge_propose(_ctx(state, "inv1"), draft_id=first["draft_id"]))
    assert not same_turn["ok"] and svc.store.entries() == []
    saved = run(tools.knowledge_propose(_ctx(state, "inv2"), draft_id=first["draft_id"]))
    assert saved["ok"] and saved["estado"].startswith("pendiente")
    [e] = svc.store.entries()
    assert e["author_id"] == "ana" and e["proposed_via"] == "agent" and e["source_session_id"] == "s1"
    again = run(tools.knowledge_propose(_ctx(state, "inv3"), draft_id=first["draft_id"]))
    assert not again["ok"]  # un borrador se usa una sola vez


# --- exportación OKF ---------------------------------------------------------------------------


def test_okf_export_only_shared_approved_and_conformant(svc, tmp_path):
    approved = propose(svc, user="ana", target_type="column", target_ref=f"{TABLE}.status", content="'Caído' es churn.",
                       sources=["https://wiki.example/churn"], stale_after="2099-01-01")
    run(svc.review(approved["id"], "lucia", True))
    propose(svc, user="ana", content="propuesta pendiente secreta")
    propose(svc, user="ana", visibility="private", content="nota privada secreta")
    metric = run(svc.propose(draft(target_type="metric", target_ref="churn", kind="definicion_metrica",
                                   content="Bajas sobre activos de ventas.", sql=f"SELECT 1 FROM `{TABLE}`"), "beto"))["entry"]
    run(svc.review(metric["id"], "lucia", True))

    out = tmp_path / "okf"  # se exportó solo al aprobar
    assert okf_export.check_conformance(out) == []
    text = "\n".join(f.read_text(encoding="utf-8") for f in out.rglob("*.md"))
    assert "secreta" not in text
    table_doc = (out / "tables" / "p.ds.ventas.md").read_text(encoding="utf-8")
    assert 'type: "BigQuery Table"' in table_doc and 'resource: "bigquery://p.ds.ventas"' in table_doc
    assert '"human:ana"' in table_doc and '"human:lucia"' in table_doc
    assert 'stale_after: "2099-01-01T00:00:00Z"' in table_doc
    assert "[^s1]: [https://wiki.example/churn](https://wiki.example/churn)" in table_doc
    assert "| `status` | STRING | Estado de la suscripción |" in table_doc
    metric_doc = (out / "metrics" / "churn.md").read_text(encoding="utf-8")
    assert "# Related" in metric_doc and "(../tables/p.ds.ventas.md)" in metric_doc
    index = (out / "index.md").read_text(encoding="utf-8")
    assert index.startswith('---\nokf_version: "0.2"\n---')
    log = (out / "log.md").read_text(encoding="utf-8")
    assert log.startswith("# Registro de cambios") and "## " in log and "**Alta**" in log


def test_okf_export_refuses_foreign_folder(svc, tmp_path):
    foreign = tmp_path / "mis-docs"
    foreign.mkdir()
    (foreign / "notas.md").write_text("mías", encoding="utf-8")
    with pytest.raises(okf_export.ExportError):
        svc.export(foreign)
    assert (foreign / "notas.md").exists()


# --- API ---------------------------------------------------------------------------------------


def test_api_flow(svc):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_dashboards.server.knowledge_api import build_router

    who = {"user": "ana"}
    app = FastAPI()
    app.include_router(build_router(lambda _r: who["user"], svc))
    client = TestClient(app)

    r = client.post("/api/knowledge/entries", json=draft(kind="advertencia", content="Junio duplicado."))
    assert r.status_code == 200 and r.json()["entry"]["status"] == "proposed"
    eid = r.json()["entry"]["id"]
    assert client.post("/api/knowledge/entries", json=draft(kind="nada")).status_code == 400
    assert client.post(f"/api/knowledge/entries/{eid}/review", json={"action": "approve"}).status_code == 403
    assert client.post("/api/knowledge/catalog/sync").status_code == 403

    who["user"] = "lucia"
    overview = client.get("/api/knowledge").json()
    assert overview["curator"] and overview["pending"] == 1 and overview["tables"][0]["proposed"] == 1
    assert client.get("/api/knowledge/entries", params={"status": "proposed"}).json()["entries"][0]["can_review"]
    r = client.post(f"/api/knowledge/entries/{eid}/review", json={"action": "approve", "note": "ok"})
    assert r.json()["entry"]["status"] == "approved"
    target = client.get(f"/api/knowledge/target/{TABLE}").json()
    assert target["catalog"][0]["ref"] == TABLE and target["entries"][0]["id"] == eid
    assert client.get("/api/knowledge/entries", params={"q": "junio"}).json()["entries"][0]["id"] == eid
    assert client.get(f"/api/knowledge/entries/{eid}/history").json()["events"][-1]["action"] == "approved"
    assert client.get("/api/knowledge/entries/kb_nada/history").status_code == 404
    assert client.post("/api/knowledge/export").json()["files"] >= 4


def test_tables_outside_enabled_datasets_are_rejected_with_a_hint(svc):
    # La ficha de la tabla de origen (otro proyecto) no se guarda con ese nombre: quedaría huérfana.
    with pytest.raises(KnowledgeError, match=r"no está en los datasets habilitados\. ¿Es p\.ds\.ventas\?"):
        propose(svc, target_ref="origen.crudo.ventas")
    with pytest.raises(KnowledgeError, match="datasets habilitados"):
        propose(svc, target_type="column", target_ref="origen.crudo.ventas.status", content="x")
    with pytest.raises(KnowledgeError, match="Los datasets habilitados son: p.ds"):
        propose(svc, target_ref="origen.crudo.otra_tabla")
    # Una tabla de un dataset habilitado que todavía no está en el catálogo se acepta, con aviso.
    r = run(svc.propose(draft(target_ref="p.ds.nueva"), "ana"))
    assert r["warnings"] and r["entry"]["target_ref"] == "p.ds.nueva"


def test_retarget_moves_table_and_column_knowledge(svc, monkeypatch, wh):
    from agent_dashboards.knowledge import retarget

    monkeypatch.setattr(config, "WAREHOUSE_DATASETS", [])  # para poder guardar sobre el nombre equivocado
    monkeypatch.setattr(retarget, "service", svc)
    t = propose(svc, target_ref="origen.crudo.ventas", content="Ventas.")
    c = propose(svc, target_type="column", target_ref="origen.crudo.ventas.status", content="Estado.")
    x = propose(svc, target_type="column", target_ref="origen.crudo.ventas.no_existe", content="Otra.")
    assert retarget.plan("origen.crudo.ventas", TABLE)["missing_columns"] == ["no_existe"]
    result = retarget.apply("origen.crudo.ventas", TABLE, "lucia")
    assert len(result["moves"]) == 2
    assert svc.store.get(t["id"])["target_ref"] == TABLE
    assert svc.store.get(c["id"])["target_ref"] == f"{TABLE}.status"
    assert svc.store.get(x["id"])["target_ref"] == "origen.crudo.ventas.no_existe"  # no se tocó
    assert {e["id"] for e in svc.lookup("ventas", "ana")["entries"]} == {t["id"], c["id"]}
    assert svc.store.events([t["id"]])[-1]["action"] == "retargeted"
