"""Dataset y conocimiento de demo: el SQL sirve para el sandbox y el conocimiento se carga entero."""

import asyncio
import re

from agent_dashboards import config
from agent_dashboards.demo import knowledge, seed
from agent_dashboards.knowledge.service import KnowledgeService
from agent_dashboards.knowledge.store import KnowledgeStore


def test_script_creates_every_table_without_dml():
    sql = seed.script("mi-proyecto", "demo_andes_outdoor")
    assert "CREATE SCHEMA IF NOT EXISTS `mi-proyecto.demo_andes_outdoor`" in sql
    tables = re.findall(r"CREATE OR REPLACE TABLE `mi-proyecto\.demo_andes_outdoor\.(\w+)`", sql)
    assert tables == ["productos", "clientes", "items_pedido", "pedidos", "eventos_web", "ventas_diarias"]
    # El sandbox de BigQuery no admite DML: todo es CREATE TABLE … AS SELECT.
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE)\b", sql)
    assert "PARTITION BY fecha" in sql and f"INTERVAL {seed.DAYS} DAY" in sql
    assert seed.DAYS < 60  # el sandbox borra lo de más de 60 días


def test_check_queries_inline_their_dependencies():
    checks = dict(seed.check_queries())
    assert set(checks) == {"productos", "clientes", "items_pedido", "pedidos", "eventos_web", "ventas_diarias"}
    assert "`" not in checks["ventas_diarias"]  # no lee tablas: se valida con un dry run sin crear nada


class FakeWarehouse:
    async def dry_run(self, sql):
        return 1024


def test_demo_knowledge_loads_approved_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WAREHOUSE_DATASETS", ["mi-proyecto.demo_andes_outdoor"])
    monkeypatch.setattr(config, "KNOWLEDGE_CURATORS", {"cura@empresa.com"})
    svc = KnowledgeService(KnowledgeStore(tmp_path / "k.db"), warehouse=FakeWarehouse(), okf_dir=tmp_path / "okf")
    ids = asyncio.run(knowledge.load(svc, "mi-proyecto", "demo_andes_outdoor", "cura@empresa.com"))
    assert len(ids) == len(knowledge.entries("mi-proyecto", "demo_andes_outdoor"))
    assert {e["status"] for e in svc.store.entries()} == {"approved"}
    assert asyncio.run(knowledge.load(svc, "mi-proyecto", "demo_andes_outdoor", "cura@empresa.com")) == []
    found = svc.lookup("pedidos", "otra@empresa.com")
    assert any(e["kind"] == "regla_filtro" for e in found["entries"])
