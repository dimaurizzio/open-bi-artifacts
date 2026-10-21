"""Ruteo de modelo por tarea dentro de un solo agente.

Cada llamada al modelo pasa por `route_model` (before_model_callback). Si en el
turno actual se cargó una skill con modelo asignado en `config.MODEL_ROUTES`, se
usa ese modelo para el resto del turno; si no, el modelo base del agente.

Funciona porque LiteLlm usa `llm_request.model` cuando viene completo. Cambiar
de modelo invalida la caché de prompts, así que se cambia como mucho una vez por
turno (al cargar la skill), no en cada llamada.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from . import config

logger = logging.getLogger("agent_dashboards.routing")

# Estado de sesión: {"invocation": id del turno, "model": ..., "skill": ...}
_ROUTE_KEY = "model_route"


def track_skill_route(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: Any
) -> None:
    """after_tool_callback: al cargar una skill ruteada, fija el modelo del turno."""
    if tool.name != "load_skill":
        return None
    skill = args.get("skill_name")
    model = config.MODEL_ROUTES.get(skill or "")
    failed = isinstance(tool_response, dict) and "error" in tool_response
    if model and not failed:
        tool_context.state[_ROUTE_KEY] = {
            "invocation": tool_context.invocation_id,
            "model": model,
            "skill": skill,
        }
    return None


def current_model(callback_context: CallbackContext) -> str:
    route = callback_context.state.get(_ROUTE_KEY) or {}
    if route.get("invocation") == callback_context.invocation_id and route.get("model"):
        return route["model"]
    return config.AGENT_MODEL


def route_model(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """before_model_callback: elige el modelo de esta llamada."""
    model = current_model(callback_context)
    if llm_request.model != model:
        logger.info("modelo %s (antes %s)", model, llm_request.model)
    llm_request.model = model
    callback_context.state["temp:model_in_use"] = model
    return None
