"""Sugerencias de próximas consultas, generadas con el modelo base a partir de tu uso.

- Inicio (chat nuevo): tus chats y artifacts recientes y las tablas del warehouse.
- Seguimiento (chat abierto): lo último que se preguntó y respondió en ese chat.

Se cachean por un rato para no gastar una llamada por cada vez que se abre la app.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import litellm

from .. import config
from ..warehouse.mcp_client import WarehouseError, client as warehouse

logger = logging.getLogger("agent_dashboards.suggestions")

FALLBACK = [
    {"title": "¿Qué tablas hay?", "label": "en el warehouse", "prompt": "¿Qué tablas hay en el warehouse? Contame brevemente qué tiene cada una."},
    {"title": "Armame un análisis", "label": "de una tabla", "prompt": "Armame un análisis de la tabla más usada del warehouse."},
    {"title": "Quiero un dashboard", "label": "con filtro por fecha", "prompt": "Quiero un dashboard con filtro por fecha."},
]

_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_tables: tuple[float, list[str]] | None = None
_TTL = 600


async def _warehouse_tables() -> list[str]:
    global _tables
    if _tables and time.time() - _tables[0] < 3600:
        return _tables[1]
    names: list[str] = []
    for full in config.WAREHOUSE_DATASETS[:3]:
        project, dataset = full.split(".", 1)
        try:
            payload = await warehouse.call_tool("list_table_ids", {"projectId": project, "datasetId": dataset, "pageSize": 60})
            names += [str(t.get("id", "")).split(".")[-1] for t in payload.get("tables", [])]
        except WarehouseError as e:
            logger.info("No se pudieron listar tablas de %s: %s", full, e)
    _tables = (time.time(), names)
    return names


def _parse(text: str) -> list[dict[str, str]]:
    match = re.search(r"\[.*\]", text, re.S)
    items = json.loads(match.group(0) if match else text)
    out = []
    for it in items[:3]:
        prompt = str(it.get("prompt", "")).strip()
        if prompt:
            out.append({"title": str(it.get("title") or prompt)[:40], "label": str(it.get("label", ""))[:50], "prompt": prompt[:300]})
    return out


async def generate(key: str, context: dict[str, Any], follow_up: bool) -> list[dict[str, str]]:
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < _TTL:
        return cached[1]
    if not follow_up:
        context = {**context, "tablas_del_warehouse": await _warehouse_tables()}
    task = (
        "Proponé 3 preguntas de seguimiento que el usuario probablemente quiera hacer a continuación, "
        "concretas y basadas en lo que se acaba de responder (profundizar, comparar, armar un dashboard o análisis)."
        if follow_up
        else "Proponé 3 consultas para empezar un chat nuevo, basadas en los temas, tablas y artifacts recientes del "
        "usuario: retomar algo, profundizar o armar un análisis o dashboard. Si no hay historial, partí de las tablas."
    )
    prompt = (
        "Sos el asistente de una herramienta de análisis de datos sobre BigQuery. "
        f"{task} Escribí en español rioplatense. Cada una con: title (2 a 5 palabras), label (complemento corto) y "
        "prompt (el mensaje completo que se enviaría). Respondé SOLO un array JSON.\n\n"
        f"Contexto:\n{json.dumps(context, ensure_ascii=False)[:6000]}"
    )
    try:
        res = await litellm.acompletion(
            model=config.AGENT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,  # el modelo razona antes de responder
        )
        items = _parse(res.choices[0].message.content or "")
    except Exception as e:  # noqa: BLE001 - las sugerencias nunca rompen la app
        logger.warning("No se pudieron generar sugerencias: %s", e)
        items = []
    if not items:
        # Si falló, se reintenta pronto en vez de dejar el fallback cacheado.
        _cache[key] = (time.time() - _TTL + 30, [] if follow_up else FALLBACK)
        return _cache[key][1]
    _cache[key] = (time.time(), items)
    return items
