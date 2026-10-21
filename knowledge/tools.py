"""Herramientas del agente sobre la base de conocimiento.

`knowledge_get` y `knowledge_search` devuelven cada entrada con su procedencia.
`knowledge_propose` va en dos pasos y el segundo tiene que llegar en otro turno:
1. Sin `draft_id`: valida y devuelve cómo quedaría (no guarda nada).
2. Con `draft_id`, después de que el usuario confirmó en un mensaje nuevo: guarda.
Así guardar siempre depende de una respuesta del usuario, no de que el modelo se acuerde.
"""

from __future__ import annotations

import secrets
from typing import Any, Optional

from google.adk.tools.tool_context import ToolContext

from .service import KnowledgeError, service

_DRAFTS = "kb_drafts"
_MAX_DRAFTS = 10


def knowledge_get(target: str, tool_context: ToolContext) -> dict[str, Any]:
    """Todo lo que se sabe de una tabla (con sus columnas), métrica o término: el
    catálogo de BigQuery y el conocimiento guardado, con quién lo escribió y si está
    aprobado. Usala antes de consultar una tabla documentada en el índice.

    Args:
      target: `proyecto.dataset.tabla`, solo el nombre de la tabla, o el nombre de una métrica o término.
    """
    return service.lookup_for_agent(target, tool_context.user_id)


def knowledge_search(text: str, tool_context: ToolContext) -> dict[str, Any]:
    """Busca en el conocimiento guardado y en el catálogo por palabras (nombres de
    tablas, columnas, métricas, términos del negocio).

    Args:
      text: Palabras a buscar, por ejemplo "churn suscriptores" o "bots".
    """
    return service.search_for_agent(text, tool_context.user_id)


async def knowledge_propose(
    tool_context: ToolContext,
    target_type: str = "",
    target_ref: str = "",
    kind: str = "",
    content: str = "",
    visibility: str = "private",
    sql: str = "",
    sources: Optional[list[str]] = None,
    stale_after: str = "",
    supersedes_id: str = "",
    draft_id: str = "",
) -> dict[str, Any]:
    """Guarda conocimiento que aportó el usuario, en dos pasos. Primero llamala sin
    draft_id: devuelve cómo quedaría y un draft_id, sin guardar. Mostráselo al usuario
    y preguntale si lo guarda. Solo si confirma en su próximo mensaje, llamala con
    draft_id (y nada más) para guardarlo. El autor es el usuario.

    Args:
      target_type: 'table', 'column', 'metric', 'term' o 'join'.
      target_ref: `proyecto.dataset.tabla`, `proyecto.dataset.tabla.columna`, o el nombre de la métrica, término o join.
        Tablas y columnas: siempre una tabla de los datasets habilitados (la del catálogo). Si el usuario
        documenta la tabla de origen que lee una habilitada, guardalo sobre la habilitada y nombrá el
        origen en el contenido o en sources.
      kind: 'descripcion', 'definicion_metrica' (con sql), 'regla_filtro', 'advertencia', 'join', 'sinonimo' o 'consulta_ejemplo' (con sql).
      content: El conocimiento, en una o dos oraciones claras y autocontenidas.
      visibility: 'private' (nota solo del usuario, sin aprobación) o 'shared' (para todos; la aprueba un curador).
      sql: SQL de solo lectura, sin parámetros, si aplica.
      sources: De dónde sale: enlaces, documentos o "conversación con el usuario".
      stale_after: AAAA-MM-DD desde la que deja de valer (por ejemplo, una advertencia sobre un mes puntual).
      supersedes_id: Id de la entrada que esta corrige (la reemplaza al guardarse o aprobarse).
      draft_id: Solo en el segundo paso, con la confirmación del usuario.
    """
    user = tool_context.user_id
    drafts: dict[str, Any] = dict(tool_context.state.get(_DRAFTS) or {})
    if draft_id:
        draft = drafts.get(draft_id)
        if not draft:
            return {"ok": False, "error": f"No existe el borrador {draft_id}. Volvé a llamar sin draft_id."}
        if draft["invocation_id"] == tool_context.invocation_id:
            return {
                "ok": False,
                "error": "Para guardar hace falta que el usuario lo confirme en un mensaje nuevo. Mostrale el borrador y esperá su respuesta.",
            }
        try:
            result = await service.propose(
                draft["entry"], user, proposed_via="agent", session_id=tool_context.session.id
            )
        except KnowledgeError as e:
            return {"ok": False, "error": str(e)}
        drafts.pop(draft_id, None)
        tool_context.state[_DRAFTS] = drafts
        e = result["entry"]
        return {
            "ok": True,
            "guardado": e["id"],
            "procedencia": e["provenance"],
            "estado": "pendiente de aprobación de un curador" if e["status"] == "proposed" else "vigente",
            "advertencias": result["warnings"],
        }

    raw = {
        "target_type": target_type,
        "target_ref": target_ref,
        "kind": kind,
        "content": content,
        "visibility": visibility,
        "sql": sql,
        "sources": sources or [],
        "stale_after": stale_after,
        "supersedes_id": supersedes_id,
    }
    try:
        entry, warnings = service.validate(raw, user)
    except KnowledgeError as e:
        return {"ok": False, "error": str(e)}
    new_id = "d_" + secrets.token_hex(4)
    drafts[new_id] = {"entry": entry, "invocation_id": tool_context.invocation_id}
    tool_context.state[_DRAFTS] = dict(list(drafts.items())[-_MAX_DRAFTS:])
    shared = entry["visibility"] == "shared"
    return {
        "ok": True,
        "draft_id": new_id,
        "borrador": {
            "sobre": f"{entry['target_type']} {entry['target_ref']}",
            "tipo": entry["kind"],
            "contenido": entry["content"],
            "sql": entry["sql"],
            "fuentes": [s.get("title") or s.get("resource") for s in entry["sources"]],
            "vence": entry["stale_after"],
            "visibilidad": "compartido (lo aprueba un curador)" if shared else "nota privada (solo la ve el usuario)",
            "corrige": entry["supersedes_id"],
        },
        "advertencias": warnings,
        "siguiente_paso": (
            "No está guardado. Mostrale al usuario cómo quedaría y preguntale si lo guarda."
            " Solo si confirma en su próximo mensaje, llamá knowledge_propose(draft_id=...)."
        ),
    }


KNOWLEDGE_TOOLS = [knowledge_get, knowledge_search, knowledge_propose]
