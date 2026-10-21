"""Herramientas del agente para el tipo Dashboard (contrato 2).

Crear un dashboard, `write_db`/`read_db` sobre su almacén, `upload_asset`/`delete_asset`
para archivos, y una corrida de la llamada de una fuente tal como quedó guardada. Las activa la skill `dashboard` al cargarse.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from google.adk.tools.base_tool import BaseTool
# Privada en ADK: lee qué skills cargó el agente (la misma que usa SkillToolset).
from google.adk.tools.skill_toolset import _read_activated_skills
from google.adk.tools.tool_context import ToolContext

from .. import config
from .dashboard import DashError, service


def _fail(e: Exception, aid: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "error": str(e)}
    if aid:
        out["artifact_id"] = aid
    return out


def _parse(data: Any) -> Any:
    if isinstance(data, str):
        try:
            return json.loads(data)
        except ValueError:
            return data
    return data


def create_dashboard(title: str, tool_context: ToolContext) -> dict[str, Any]:
    """Crea un dashboard vacío desde el tipo y lo abre al lado del chat.

    Args:
      title: Lo que la persona le llama, no "Dashboard".
    """
    try:
        aid = service.store.create(title.strip() or "Dashboard", owner=tool_context.user_id)
    except DashError as e:
        return _fail(e)
    return {"ok": True, "artifact_id": aid, "url": f"/a/{aid}", "next": "Llená este: write_db db_op='batch' con dash/meta, dash/params, datasets/<id> y files/index.html."}


def write_db(
    dashboard_id: str,
    db_op: str,
    tool_context: ToolContext,
    collection: str = "",
    doc_id: str = "",
    data: Optional[Any] = None,
    writes: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Escribe documentos del dashboard. Solo editores.

    Args:
      dashboard_id: Id que devolvió create_dashboard.
      db_op: 'set' (reemplaza), 'update' (mezcla campos; null borra una clave), 'delete' o 'batch'.
      collection: 'dash', 'datasets' o 'files' (no en batch).
      doc_id: 'meta' o 'params' en dash; el id del dataset; el nombre del archivo ('index.html').
      data: El documento (objeto). Un archivo es {"text": "..."} con el archivo entero.
      writes: Solo en batch: hasta 50 [{db_op, collection, doc_id, data}], 1 MB en total.
    """
    try:
        if db_op == "batch":
            items = _parse(writes) or []
            if not isinstance(items, list):
                raise DashError("writes es una lista de {db_op, collection, doc_id, data}.")
            batch = [{**w, "op": w.get("db_op") or w.get("op"), "data": _parse(w.get("data"))} for w in items]
        else:
            batch = [{"op": db_op, "collection": collection, "doc_id": doc_id, "data": _parse(data)}]
        result = service.write(dashboard_id, tool_context.user_id, batch)
    except (DashError, AttributeError, TypeError) as e:
        return _fail(e, dashboard_id)
    return {**result, "artifact_id": dashboard_id}


def read_db(dashboard_id: str, collection: str, tool_context: ToolContext, doc_id: str = "") -> dict[str, Any]:
    """Lee un documento, o todos los de una colección si doc_id está vacío.

    Args:
      dashboard_id: Id del dashboard.
      collection: 'dash', 'datasets', 'files', o 'data/users/me' (doc 'runs': el documento de corridas).
      doc_id: Id del documento; vacío lista la colección.
    """
    try:
        doc = service.read(dashboard_id, collection, doc_id or None, tool_context.user_id)
    except DashError as e:
        return _fail(e)
    if doc is None:
        return {"ok": True, "exists": False}
    return {"ok": True, "data": doc}


async def upload_asset(
    dashboard_id: str,
    name: str,
    tool_context: ToolContext,
    content: str = "",
    from_request: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Sube un archivo (CSV, TSV o JSON, hasta 8 MB) al dashboard y devuelve su url.

    Args:
      dashboard_id: Id del dashboard.
      name: Nombre del archivo con extensión (ventas.csv, filas.json).
      content: El texto del archivo, cuando lo tenés (datos que dio la persona, filas fijas).
      from_request: En vez de content, una consulta {connector, tool, args} sin filtros: se corre
        y sus filas se guardan como .json (resultados guardados de una consulta pausada).
    """
    try:
        if from_request:
            return {"ok": True, **await service.upload_from_request(dashboard_id, tool_context.user_id, name, _parse(from_request))}
        service.require_editor(dashboard_id, tool_context.user_id)
        if not content:
            raise DashError("Falta content o from_request.")
        asset = service.store.put_asset(dashboard_id, name, content.encode("utf-8"))
    except DashError as e:
        return _fail(e)
    return {"ok": True, **asset}


def delete_asset(dashboard_id: str, url: str, tool_context: ToolContext) -> dict[str, Any]:
    """Borra un archivo del dashboard (/_blob/<id>) que ya no nombra ningún dataset ni archivo de la página.

    Args:
      dashboard_id: Id del dashboard.
      url: La url del archivo, /_blob/<id>.
    """
    try:
        service.require_editor(dashboard_id, tool_context.user_id)
        datasets = service.store.list_docs(dashboard_id, "datasets")
        files = service.store.list_docs(dashboard_id, "files")
        used = [i for i, d in datasets.items() if url in json.dumps(d)] + [n for n, f in files.items() if url in f.get("text", "")]
        if used:
            raise DashError(f"Lo usan todavía: {', '.join(used)}.")
        return {"ok": service.store.delete_asset(dashboard_id, url)}
    except DashError as e:
        return _fail(e)


async def run_dataset(
    dashboard_id: str, dataset_id: str, tool_context: ToolContext, values: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Corre la llamada de un dataset tal como quedó guardada, con los filtros en sus valores por defecto
    (o los de `values`). Devuelve columnas y una muestra de filas.

    Args:
      dashboard_id: Id del dashboard.
      dataset_id: Id del dataset.
      values: Valores de filtros a probar; los que falten toman su default de dash/params (o NULL).
    """
    try:
        out = await service.run_dataset(dashboard_id, dataset_id, _parse(values) or {}, user=tool_context.user_id)
    except DashError as e:
        return {"ok": False, "status": e.status, "error": str(e)}
    data = out.get("data")
    sample = data[: config.AGENT_MAX_ROWS] if isinstance(data, list) else data
    return {
        "ok": True,
        "status": "ok",
        "columns": out.get("columns"),
        "rows": out.get("rows"),
        "cut": out.get("cut"),
        "sample": sample,
        "args": out.get("args"),
        "meta": out.get("meta"),
        "note": "Con loader, la página arma las filas desde la respuesta: esto es la llamada sin loader." if "answer" in out else None,
    }


def list_dashboards(tool_context: ToolContext) -> dict[str, Any]:
    """Lista los dashboards que la persona puede ver: los suyos y los de sus proyectos (los más recientes primero)."""
    user = tool_context.user_id
    items = [d for d in service.store.list() if service.can_view(d, user)]
    items.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    return {"dashboards": [
        {**{k: d.get(k) for k in ("id", "title", "updated_at", "project_id")}, "editable": service.can_edit(d, user)}
        for d in items[:50]
    ]}


DASHBOARD_TOOLS = [create_dashboard, write_db, read_db, upload_asset, delete_asset, run_dataset, list_dashboards]

# Las herramientas están declaradas desde el primer turno: la lista de herramientas
# tiene que ser la misma en toda la conversación, porque los modelos que atan su
# razonamiento a ella (thinking preservado) rechazan el historial si cambia. Lo que
# antes garantizaba activarlas al cargar la skill (leer las reglas antes de escribir)
# lo garantiza esta guarda.
_WRITE_TOOLS = {"create_dashboard", "write_db", "upload_asset", "delete_asset"}
_SKILL = "dashboard"


def require_dashboard_skill(tool: BaseTool, args: dict[str, Any], tool_context: ToolContext) -> dict[str, Any] | None:
    """before_tool_callback: escribir un dashboard exige haber cargado la skill `dashboard`."""
    if tool.name not in _WRITE_TOOLS:
        return None
    if _SKILL in _read_activated_skills(tool_context.state, tool_context.agent_name):
        return None
    return {"error": f"Antes de escribir un dashboard cargá la skill `{_SKILL}` con load_skill: ahí están las reglas."}

