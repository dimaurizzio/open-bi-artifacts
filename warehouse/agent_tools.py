"""Acceso del agente al warehouse: McpToolset + callbacks que fuerzan las reglas.

Los callbacks hacen de "hook" sobre cada llamada a BigQuery: no dependen de que
el modelo recuerde las convenciones (ver "Riesgo de drift" en docs/).
"""

from __future__ import annotations

from typing import Any

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from google.adk.tools.tool_context import ToolContext

from .. import config
from . import sql_guard
from .credentials import client_for, headers_for
from .mcp_client import WarehouseError, payload_from_adk_response, rows_from_payload

_SQL_TOOLS = {"execute_sql_readonly"}
_DATASET_TOOLS = {"get_dataset_info", "list_table_ids", "get_table_info"}
_RESULT_TOOLS = {"execute_sql_readonly", "get_query_results"}


def build_bigquery_toolset() -> McpToolset:
    """Herramientas del servidor MCP remoto de BigQuery, solo las de lectura."""
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=config.BIGQUERY_MCP_URL, timeout=30, sse_read_timeout=300
        ),
        tool_filter=config.AGENT_MCP_TOOLS,
        # Cada llamada del agente va con las credenciales de quien chatea (QUERY_CREDENTIALS).
        header_provider=lambda ctx: headers_for(ctx.user_id if ctx else None),
    )


async def guard_warehouse_call(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    """before_tool_callback: valida y completa la llamada antes de que salga."""
    try:
        if tool.name in _SQL_TOOLS:
            sql = args.get("query") or ""
            if sql_guard.find_params(sql):
                return {"error": "En exploración no se usan parámetros {{...}}: escribí el valor literal."}
            sql_guard.check_readonly(sql)
            sql_guard.check_datasets(sql)
            args["projectId"] = config.WAREHOUSE_PROJECT
            args.setdefault("jobTimeoutMs", "120000")
            args["labels"] = {**(args.get("labels") or {}), "app": "agent-dashboards"}
            if not args.get("dryRun"):
                scanned = await client_for(tool_context.user_id).dry_run(sql)
                if scanned > config.MAX_BYTES_SCANNED:
                    return {
                        "error": (
                            f"La consulta escanearía {scanned / 1024**3:.1f} GiB y el límite es"
                            f" {config.MAX_BYTES_SCANNED / 1024**3:.0f} GiB. Filtrá por partición"
                            " o usá una tabla agregada."
                        )
                    }
        elif tool.name in _DATASET_TOOLS:
            args["projectId"] = args.get("projectId") or config.WAREHOUSE_PROJECT
            sql_guard.check_dataset_id(args["projectId"], args.get("datasetId"))
    except (sql_guard.SqlGuardError, WarehouseError) as e:
        return {"error": str(e)}
    return None


def compact_warehouse_result(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: Any
) -> dict[str, Any] | None:
    """after_tool_callback: convierte el formato f/v de BigQuery en filas legibles."""
    payload = payload_from_adk_response(tool_response)
    if payload is None:
        return None
    if tool.name == "list_dataset_ids" and config.WAREHOUSE_DATASETS:
        allowed = {d.split(".", 1)[1].lower() for d in config.WAREHOUSE_DATASETS}
        datasets = [
            d for d in payload.get("datasets", [])
            if str(d.get("id", d)).split(".")[-1].split(":")[-1].lower() in allowed
        ]
        return {"datasets": datasets}
    if tool.name not in _RESULT_TOOLS or "schema" not in payload:
        return None
    if args.get("dryRun"):
        return {"dry_run": True, "bytes_processed": int(payload.get("totalBytesProcessed") or 0)}
    if not payload.get("jobComplete", True):
        return {"job_complete": False, "job": payload.get("jobReference"), "nota": "Usá get_query_results."}
    columns, rows = rows_from_payload(payload)
    total = int(payload.get("totalRows") or len(rows))
    shown = rows[: config.AGENT_MAX_ROWS]
    result = {
        "columns": columns,
        "rows": shown,
        "total_rows": total,
        "bytes_processed": int(payload.get("totalBytesProcessed") or 0),
    }
    if total > len(shown):
        result["nota"] = (
            f"Se muestran {len(shown)} de {total} filas. Agregá en SQL en vez de traer detalle."
        )
    return result
