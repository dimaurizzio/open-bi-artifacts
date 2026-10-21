"""Tools del agente sobre artifacts.

No se le dan al agente desde el arranque: las activa una skill al cargarse
(metadata `adk_additional_tools` en su SKILL.md). Así las instrucciones del tipo
siempre llegan antes que la capacidad de escribir.
"""

from __future__ import annotations

from typing import Any, Optional

from google.adk.tools.tool_context import ToolContext

from .service import ArtifactError, service


def _fail(e: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(e)}


def artifact_create(kind: str, title: str, description: str, tool_context: ToolContext) -> dict[str, Any]:
    """Crea un artifact vacío y devuelve su id.

    Args:
      kind: 'dashboard' (consultas en vivo, filtros) o 'analysis' (análisis ad hoc con resultados guardados).
      title: Título visible.
      description: Una oración sobre qué responde.
    """
    try:
        meta = service.create(kind=kind, title=title, description=description, owner=tool_context.user_id)
    except ArtifactError as e:
        return _fail(e)
    return {"ok": True, "artifact_id": meta["id"], "kind": kind, "url": f"/a/{meta['id']}"}


def artifact_list(tool_context: ToolContext) -> dict[str, Any]:
    """Lista los artifacts del usuario (más recientes primero)."""
    items = service.store.list(owner=tool_context.user_id)
    return {"artifacts": [{k: m.get(k) for k in ("id", "kind", "title", "updated_at")} for m in items[:50]]}


def artifact_read(artifact_id: str, path: str, tool_context: ToolContext) -> dict[str, Any]:
    """Lee un documento del artifact, o su resumen completo si path está vacío.

    Args:
      artifact_id: Id del artifact.
      path: '' para el resumen; o dash/meta, dash/params, datasets/<id>, files/index.html.
    """
    try:
        if not path:
            return {"ok": True, **service.describe(artifact_id)}
        service.require(artifact_id)
        content = service.store.read_doc(artifact_id, path)
    except Exception as e:  # noqa: BLE001 - el error vuelve al modelo
        return _fail(e)
    if content is None:
        return {"ok": False, "error": f"{path} no existe todavía."}
    return {"ok": True, "path": path, "content": content}


def artifact_write(artifact_id: str, path: str, content: str, tool_context: ToolContext) -> dict[str, Any]:
    """Escribe (reemplaza) un documento del artifact. Las reglas forzadas se validan acá.

    Args:
      artifact_id: Id del artifact.
      path: dash/meta | dash/params | datasets/<id> | files/index.html.
      content: JSON (como texto) para dash/* y datasets/*; HTML para files/index.html. Vacío en datasets/<id> lo borra.
    """
    try:
        result = service.write(artifact_id, path, content, tool_context.user_id)
    except ArtifactError as e:
        return _fail(e)
    return {**result, "artifact_id": artifact_id, "url": f"/a/{artifact_id}"}


async def artifact_test_dataset(
    artifact_id: str, dataset_id: str, tool_context: ToolContext, params: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Corre una vez la consulta de un dataset 'live' para conocer columnas y una muestra.

    Args:
      artifact_id: Id del artifact.
      dataset_id: Id del dataset (datasets/<id>).
      params: Valores de filtros para la prueba; si falta alguno se usa su default.
    """
    try:
        return await service.test_dataset(artifact_id, dataset_id, params, tool_context.user_id)
    except ArtifactError as e:
        return _fail(e)


async def artifact_save_results(artifact_id: str, dataset_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Corre la consulta de un dataset 'saved' y guarda el resultado (la foto que ven todos).

    Args:
      artifact_id: Id del artifact.
      dataset_id: Id del dataset en modo 'saved'.
    """
    try:
        ds = service.store.datasets(artifact_id).get(dataset_id) or {}
        if ds.get("mode") != "saved":
            return {"ok": False, "error": "Solo para datasets en modo 'saved'. Para 'live' usá artifact_test_dataset."}
        return await service.test_dataset(artifact_id, dataset_id, None, tool_context.user_id)
    except ArtifactError as e:
        return _fail(e)


def artifact_runs(artifact_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Lee el documento de corridas: fuentes que fallaron, números que no coinciden y errores de la página.

    Args:
      artifact_id: Id del artifact.
    """
    try:
        return {"ok": True, **service.runs_summary(artifact_id)}
    except ArtifactError as e:
        return _fail(e)


ARTIFACT_TOOLS = [
    artifact_create,
    artifact_list,
    artifact_read,
    artifact_write,
    artifact_test_dataset,
    artifact_save_results,
    artifact_runs,
]
