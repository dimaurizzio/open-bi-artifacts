"""Modelo y effort elegidos por la persona en cada turno, con cualquier proveedor.

- `config.AGENT_MODELS` es el catálogo que muestra el chat. Cada modelo se nombra
  como en LiteLLM ("anthropic/claude-opus-5-5", "openai/gpt-5", "gemini/gemini-3.8-flash")
  o, para Gemini directo con ADK, sin proveedor ("gemini-3.8-flash").
- Los niveles de effort de cada modelo salen de lo que declara el mapa de modelos de
  LiteLLM (también para Gemini directo): el chat solo ofrece los que sirven.
- La elección viaja en el estado de la sesión. `apply_model_choice`
  (before_model_callback) la pasa al pedido y `TurnModel` lo manda al proveedor:
  LiteLLM recibe `reasoning_effort` y lo traduce a cada API; Gemini directo recibe
  `thinking_config.thinking_level`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from . import config

logger = logging.getLogger("agent_dashboards.models")

# Estado de sesión con la elección de la persona (lo escribe el servidor en cada turno).
MODEL_KEY = "agent_model"
EFFORT_KEY = "agent_effort"
# Etiqueta interna con la que el callback le pasa el effort a TurnModel (no sale al proveedor).
_EFFORT_LABEL = "agent_effort"

# De menor a mayor; cada proveedor acepta un subconjunto.
EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max")
EFFORT_NAMES = {
    "minimal": "Mínimo",
    "low": "Bajo",
    "medium": "Medio",
    "high": "Alto",
    "xhigh": "Muy alto",
    "max": "Máximo",
}

# Niveles de razonamiento de Gemini directo (types.ThinkingLevel); no tiene xhigh ni max.
_GEMINI_LEVELS = ("minimal", "low", "medium", "high")


@dataclass(frozen=True)
class ModelChoice:
    id: str
    name: str
    efforts: tuple[str, ...]


def is_native_gemini(model: str) -> bool:
    """Gemini sin LiteLLM: el nombre va sin proveedor ("gemini-3.8-flash")."""
    return "/" not in model and model.startswith("gemini")


def _litellm_info(model: str) -> dict[str, Any]:
    import litellm

    cost = litellm.model_cost
    return cost.get(model) or cost.get(model.split("/", 1)[-1]) or {}


def efforts_for(model: str) -> tuple[str, ...]:
    """Niveles de effort que acepta el modelo, de menor a mayor (vacío = no se regula)."""
    info = _litellm_info(model)
    if not info.get("supports_reasoning"):
        return ()
    levels = {"low", "medium", "high"}
    levels.update(level for level in ("minimal", "xhigh", "max") if info.get(f"supports_{level}_reasoning_effort"))
    if is_native_gemini(model):
        levels &= set(_GEMINI_LEVELS)
    return tuple(level for level in EFFORT_ORDER if level in levels)


def catalog() -> list[ModelChoice]:
    return [ModelChoice(model_id, label, efforts_for(model_id)) for model_id, label in config.AGENT_MODELS]


def resolve(model: str | None, effort: str | None) -> tuple[str, str | None]:
    """La elección del navegador, validada contra el catálogo (lo inválido vuelve al default)."""
    choices = {c.id: c for c in catalog()}
    choice = choices.get(model or "") or choices[config.AGENT_MODEL]
    return choice.id, (effort if effort in choice.efforts else None)


def apply_model_choice(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """before_model_callback: el modelo y el effort que eligió la persona para este turno."""
    model, effort = resolve(callback_context.state.get(MODEL_KEY), callback_context.state.get(EFFORT_KEY))
    llm_request.model = model
    if effort:
        llm_request.config = llm_request.config or types.GenerateContentConfig()
        llm_request.config.labels = {**(llm_request.config.labels or {}), _EFFORT_LABEL: effort}
    return None


class TurnModel(LiteLlm):
    """Manda cada pedido al proveedor de su modelo, con su effort.

    Hereda de LiteLlm para que ADK conserve los ids de las llamadas a herramientas,
    que Anthropic y OpenAI necesitan; delega en un LiteLlm o en un Gemini por modelo
    y effort, creados una vez.
    """

    _backends: dict[tuple[str, str | None], Any] = PrivateAttr(default_factory=dict)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        model = llm_request.model or self.model
        effort = None
        if llm_request.config and llm_request.config.labels:
            labels = dict(llm_request.config.labels)
            effort = labels.pop(_EFFORT_LABEL, None)
            llm_request.config.labels = labels or None
        if is_native_gemini(model):
            backend = self._backend(model, None)
            if effort:
                llm_request.config.thinking_config = types.ThinkingConfig(thinking_level=effort.upper())
        else:
            backend = self._backend(model, effort)
        async for response in backend.generate_content_async(llm_request, stream=stream):
            yield response

    def _backend(self, model: str, effort: str | None):
        key = (model, effort)
        if key not in self._backends:
            if is_native_gemini(model):
                from google.adk.models.google_llm import Gemini

                self._backends[key] = Gemini(model=model)
            else:
                self._backends[key] = LiteLlm(model=model, **({"reasoning_effort": effort} if effort else {}))
        return self._backends[key]
