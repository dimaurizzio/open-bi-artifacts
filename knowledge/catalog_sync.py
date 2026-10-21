"""Sincronización del catálogo desde el MCP de BigQuery.

Para cada dataset habilitado: `list_table_ids` y `get_table_info` de cada tabla.
Guarda descripción de tablas y columnas, tipos, partición, clustering, filas y
fecha de actualización. Autor `sistema`: nadie lo edita y no se mezcla con el
conocimiento de las personas.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from .. import config
from ..warehouse.mcp_client import BigQueryMcpClient, WarehouseError
from .store import KnowledgeStore, now_iso

logger = logging.getLogger("agent_dashboards.knowledge.catalog")

_PARALLEL = 4


def _split_id(raw: str) -> tuple[str, str, str]:
    """'proyecto:dataset.tabla' o 'proyecto.dataset.tabla' → (proyecto, dataset, tabla)."""
    text = raw.replace(":", ".")
    project, dataset, table = (text.split(".", 2) + ["", ""])[:3]
    return project, dataset, table


def _ms_to_iso(value: Any) -> str | None:
    try:
        return dt.datetime.fromtimestamp(int(value) / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def flatten_fields(fields: list[dict[str, Any]], prefix: str = "") -> list[dict[str, Any]]:
    """Columnas de un esquema, con los RECORD aplanados como `padre.hijo`."""
    out = []
    for f in fields or []:
        name = f"{prefix}{f.get('name', '')}"
        col = {"name": name, "type": f.get("type") or "", "mode": f.get("mode") or "NULLABLE"}
        if f.get("description"):
            col["description"] = f["description"]
        out.append(col)
        if f.get("fields"):
            out += flatten_fields(f["fields"], prefix=name + ".")
    return out


def table_from_info(info: dict[str, Any], dataset_key: str, fallback_name: str) -> dict[str, Any]:
    """Recurso Table de BigQuery (lo que devuelve get_table_info) → fila del catálogo."""
    ref = info.get("tableReference") or {}
    project, dataset, name = _split_id(str(info.get("id") or ""))
    project = ref.get("projectId") or project or dataset_key.split(".")[0]
    dataset = ref.get("datasetId") or dataset or dataset_key.split(".", 1)[-1]
    name = ref.get("tableId") or name or fallback_name
    tp = info.get("timePartitioning") or {}
    rp = info.get("rangePartitioning") or {}
    return {
        "ref": f"{project}.{dataset}.{name}",
        "dataset": dataset_key,
        "name": name,
        "table_type": info.get("type") or ("VIEW" if info.get("view") else "TABLE"),
        "description": (info.get("description") or "").strip(),
        "num_rows": _int(info.get("numRows")),
        "num_bytes": _int(info.get("numBytes")),
        "partition_field": tp.get("field") or rp.get("field"),
        "partition_type": tp.get("type") or ("RANGE" if rp else None),
        "clustering": list((info.get("clustering") or {}).get("fields") or []),
        "last_modified": _ms_to_iso(info.get("lastModifiedTime")),
        "columns": flatten_fields((info.get("schema") or {}).get("fields") or []),
    }


async def _datasets(warehouse: BigQueryMcpClient) -> list[str]:
    if config.WAREHOUSE_DATASETS:
        return list(config.WAREHOUSE_DATASETS)
    payload = await warehouse.call_tool("list_dataset_ids", {"projectId": config.WAREHOUSE_PROJECT})
    out = []
    for d in payload.get("datasets") or []:
        raw = str(d.get("id") if isinstance(d, dict) else d)
        project, dataset, _ = _split_id(raw if ("." in raw or ":" in raw) else f"{config.WAREHOUSE_PROJECT}.{raw}")
        out.append(f"{project}.{dataset}")
    return out


async def fetch(
    warehouse: BigQueryMcpClient, datasets: list[str]
) -> tuple[list[dict[str, Any]], list[str], list[str], set[str]]:
    """Lee el catálogo de BigQuery. Devuelve (tablas, datasets leídos, errores, tablas que fallaron)."""
    tables: list[dict[str, Any]] = []
    errors: list[str] = []
    failed: set[str] = set()
    done: list[str] = []
    sem = asyncio.Semaphore(_PARALLEL)

    async def one(project: str, dataset: str, table: str, key: str) -> None:
        async with sem:
            try:
                info = await warehouse.call_tool(
                    "get_table_info", {"projectId": project, "datasetId": dataset, "tableId": table}
                )
                tables.append(table_from_info(info, key, table))
            except WarehouseError as e:
                errors.append(f"{project}.{dataset}.{table}: {e}")
                failed.add(f"{project}.{dataset}.{table}")

    for key in datasets:
        project, dataset = (key.split(".", 1) + [""])[:2]
        try:
            listing = await warehouse.call_tool("list_table_ids", {"projectId": project, "datasetId": dataset})
        except WarehouseError as e:
            errors.append(f"{key}: {e}")
            continue
        names = []
        for t in listing.get("tables") or []:
            raw = str(t.get("id") if isinstance(t, dict) else t)
            names.append(_split_id(raw)[2] if ("." in raw or ":" in raw) else raw)
        await asyncio.gather(*(one(project, dataset, n, key) for n in names if n))
        done.append(key)
    return tables, done, errors, failed


async def sync(store: KnowledgeStore, warehouse: BigQueryMcpClient, datasets: list[str] | None = None) -> dict[str, Any]:
    started = now_iso()
    try:
        datasets = datasets or await _datasets(warehouse)
    except WarehouseError as e:
        result = {"at": started, "ok": False, "tables": 0, "removed": 0, "datasets": [], "errors": [str(e)]}
        store.set_meta("catalog_sync", result)
        return result
    tables, done, errors, failed = await fetch(warehouse, datasets)
    # Un dataset que falló al listarse no se toca, ni una tabla que falló al leerse.
    counts = store.replace_catalog(tables, done, keep=failed)
    result = {"at": now_iso(), "ok": not errors, "datasets": done, "errors": errors[:20], **counts}
    store.set_meta("catalog_sync", result)
    logger.info("Catálogo sincronizado: %s tablas, %s errores", counts["tables"], len(errors))
    return result
