"""Cliente directo del servidor MCP remoto de BigQuery.

El agente usa el mismo servidor a través de `McpToolset`. Este cliente lo usa la
plataforma (no el modelo) para correr las fuentes de los artifacts: es la pieza
que "media la llamada" con las credenciales del lector sin exponerlas a la página.
Hoy las credenciales son las ADC de quien corre el servidor (un solo usuario).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import google.auth
import google.auth.transport.requests
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from .. import config

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_creds_lock = threading.Lock()
_creds = None


class WarehouseError(Exception):
    """Error devuelto por BigQuery o por el servidor MCP."""


def access_token() -> str:
    """Token OAuth vigente de las Application Default Credentials."""
    global _creds
    with _creds_lock:
        if _creds is None:
            _creds, _ = google.auth.default(scopes=_SCOPES)
        if not _creds.valid:
            _creds.refresh(google.auth.transport.requests.Request())
        return _creds.token


def auth_headers() -> dict[str, str]:
    # Sin x-goog-user-project: el servidor MCP falla con ese header en get_table_info.
    # La facturación de las consultas va por el projectId de cada llamada.
    return {"Authorization": f"Bearer {access_token()}"}


@dataclass
class QueryResult:
    columns: list[dict[str, str]]
    rows: list[dict[str, Any]]
    total_rows: int
    bytes_processed: int = 0
    truncated: bool = False
    elapsed_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


def _tool_payload(result: Any) -> dict[str, Any]:
    """Extrae el JSON de una respuesta MCP (structured o texto)."""
    text = "\n".join(getattr(c, "text", "") or "" for c in (result.content or []))
    if result.is_error:
        raise WarehouseError(text or "BigQuery devolvió un error sin detalle.")
    if result.structured_content:
        return dict(result.structured_content)
    try:
        return json.loads(text)
    except (TypeError, ValueError) as e:
        raise WarehouseError(f"Respuesta MCP no es JSON: {text[:300]}") from e


def payload_from_adk_response(response: Any) -> dict[str, Any] | None:
    """Mismo parseo, pero para la respuesta de un McpTool de ADK (dict)."""
    if not isinstance(response, dict):
        return None
    structured = response.get("structuredContent") or response.get("structured_content")
    if isinstance(structured, dict):
        return structured
    for part in response.get("content") or []:
        text = part.get("text") if isinstance(part, dict) else None
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return None
    return None


# --- Conversión del formato REST de BigQuery (f/v) a filas planas --------------


def _convert(value: Any, fld: dict[str, Any]) -> Any:
    if value is None:
        return None
    if fld.get("mode") == "REPEATED":
        item = {**fld, "mode": "NULLABLE"}
        return [_convert(v.get("v") if isinstance(v, dict) else v, item) for v in value]
    typ = fld.get("type", "STRING")
    if typ in ("RECORD", "STRUCT"):
        subfields = fld.get("fields", [])
        cells = value.get("f", []) if isinstance(value, dict) else []
        return {sf["name"]: _convert(c.get("v"), sf) for sf, c in zip(subfields, cells)}
    if typ in ("INTEGER", "INT64"):
        return int(value)
    if typ in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
        return float(value)
    if typ in ("BOOLEAN", "BOOL"):
        return str(value).lower() == "true"
    if typ == "TIMESTAMP":
        s = str(value)
        secs = float(s) if ("." in s or "E" in s.upper()) else int(s) / 1e6 if len(s) > 12 else float(s)
        return dt.datetime.fromtimestamp(secs, dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def rows_from_payload(payload: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    fields = (payload.get("schema") or {}).get("fields") or []
    columns = [{"name": f["name"], "type": f.get("type", "STRING")} for f in fields]
    rows = []
    for raw in payload.get("rows") or []:
        cells = raw.get("f", [])
        rows.append({f["name"]: _convert(c.get("v"), f) for f, c in zip(fields, cells)})
    return columns, rows


# --- Cliente ---------------------------------------------------------------


class BigQueryMcpClient:
    """Abre una sesión MCP por operación (las sesiones no se comparten entre tareas)."""

    def __init__(
        self,
        url: str = config.BIGQUERY_MCP_URL,
        project: str = config.WAREHOUSE_PROJECT,
        token_provider: Callable[[], Awaitable[str]] | None = None,
    ):
        """`token_provider`: token de acceso de una persona; sin él, las credenciales del servidor."""
        self.url = url
        self.project = project
        self.token_provider = token_provider

    async def headers(self) -> dict[str, str]:
        if self.token_provider is None:
            return await asyncio.to_thread(auth_headers)
        return {"Authorization": f"Bearer {await self.token_provider()}"}

    async def _session_call(self, calls):
        headers = await self.headers()
        http_client = create_mcp_http_client(headers=headers)
        try:
            async with streamable_http_client(self.url, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await calls(session)
        except WarehouseError:
            raise
        except BaseException as e:  # anyio envuelve en ExceptionGroup
            inner = e
            while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
                inner = inner.exceptions[0]
            if isinstance(inner, WarehouseError):
                raise inner from None
            if isinstance(inner, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            msg = getattr(inner, "message", None) or str(inner) or type(inner).__name__
            raise WarehouseError(f"Fallo de BigQuery MCP: {msg}") from None

    async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        async def run(session):
            return _tool_payload(await session.call_tool(name, args))

        return await self._session_call(run)

    async def dry_run(self, sql: str) -> int:
        """Bytes que escanearía la consulta (valida la sintaxis de paso)."""
        payload = await self.call_tool(
            "execute_sql_readonly", {"projectId": self.project, "query": sql, "dryRun": True}
        )
        return int(payload.get("totalBytesProcessed") or 0)

    async def run_query(
        self,
        sql: str,
        *,
        max_bytes: int = config.MAX_RESULT_BYTES,
        max_rows: int | None = None,
        timeout_s: int = 300,
        labels: dict[str, str] | None = None,
    ) -> QueryResult:
        started = time.monotonic()

        async def run(session):
            args = {
                "projectId": self.project,
                "query": sql,
                "timeoutMs": 20000,
                "jobTimeoutMs": str(timeout_s * 1000),
                "labels": {"app": "agent-dashboards", **(labels or {})},
            }
            payload = _tool_payload(await session.call_tool("execute_sql_readonly", args))
            job = payload.get("jobReference") or {}
            job_id = job.get("jobId") or payload.get("jobId")
            location = job.get("location") or payload.get("location")
            # Consulta larga: se sigue con get_query_results hasta que termine.
            while not payload.get("jobComplete", True):
                if time.monotonic() - started > timeout_s:
                    raise WarehouseError("La consulta superó el tiempo máximo.")
                await asyncio.sleep(1.5)
                payload = _tool_payload(
                    await session.call_tool(
                        "get_query_results",
                        {"projectId": self.project, "jobId": job_id, "location": location},
                    )
                )
            columns, rows = rows_from_payload(payload)
            total = int(payload.get("totalRows") or len(rows))
            truncated = False
            size = len(json.dumps(rows, default=str))
            token = payload.get("pageToken")
            # Más páginas: se traen mientras entren en el límite de resultado.
            while token and job_id and (max_rows is None or len(rows) < max_rows):
                if size > max_bytes:
                    truncated = True
                    break
                page = _tool_payload(
                    await session.call_tool(
                        "get_query_results",
                        {"projectId": self.project, "jobId": job_id, "location": location, "pageToken": token},
                    )
                )
                _, more = rows_from_payload(page)
                rows.extend(more)
                size += len(json.dumps(more, default=str))
                token = page.get("pageToken")
            if token:
                truncated = True
            if max_rows is not None and len(rows) > max_rows:
                rows, truncated = rows[:max_rows], True
            if size > max_bytes:
                raise WarehouseError(
                    f"El resultado pesa {size / 1e6:.1f} MB y el máximo es"
                    f" {max_bytes / 1e6:.0f} MB. Agregá más en SQL."
                )
            return QueryResult(
                columns=columns,
                rows=rows,
                total_rows=total,
                bytes_processed=int(payload.get("totalBytesProcessed") or 0),
                truncated=truncated,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

        return await self._session_call(run)


client = BigQueryMcpClient()
