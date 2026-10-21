"""Modelo y effort por turno: catálogo, validación y envío a cada proveedor."""

import asyncio
from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from agent_dashboards import config, models

OPUS = "anthropic/claude-opus-5-5"
HAIKU = "anthropic/claude-haiku-5-5"
GEMINI = "gemini-3.8-flash"


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(config, "AGENT_MODEL", HAIKU)
    monkeypatch.setattr(
        config,
        "AGENT_MODELS",
        [(HAIKU, "Haiku 5.5"), (OPUS, "Opus 5.5"), ("openai/gpt-4o", "GPT-4o"), (GEMINI, "Gemini 3.8 Flash")],
    )


def test_efforts_come_from_each_provider():
    assert models.efforts_for(OPUS) == ("low", "medium", "high", "xhigh", "max")
    assert models.efforts_for("openai/gpt-4o") == ()  # sin razonamiento configurable
    # Gemini directo: los niveles de thinking_level que el modelo acepta (sin xhigh ni max).
    assert models.efforts_for(GEMINI) == ("low", "medium", "high")
    assert models.efforts_for("gemini-inexistente") == ()


def test_resolve_falls_back_to_the_default(catalog):
    assert models.resolve(OPUS, "max") == (OPUS, "max")
    assert models.resolve("openai/otro", "high") == (HAIKU, "high")
    assert models.resolve("openai/gpt-4o", "high") == ("openai/gpt-4o", None)
    assert models.resolve(None, "inventado") == (HAIKU, None)


def test_the_choice_reaches_the_request(catalog):
    ctx = SimpleNamespace(state={models.MODEL_KEY: OPUS, models.EFFORT_KEY: "xhigh"})
    req = LlmRequest(model=HAIKU, config=types.GenerateContentConfig(labels={"app": "x"}))
    models.apply_model_choice(ctx, req)
    assert req.model == OPUS
    assert req.config.labels == {"app": "x", "agent_effort": "xhigh"}


class _Fake:
    def __init__(self):
        self.seen = []

    async def generate_content_async(self, llm_request, stream=False):
        self.seen.append(llm_request)
        yield "ok"


def _run(turn, req):
    async def collect():
        return [r async for r in turn.generate_content_async(req)]

    return asyncio.run(collect())


def test_turn_model_sends_effort_to_litellm_and_hides_the_label(monkeypatch):
    turn = models.TurnModel(model=HAIKU)
    made = []

    def backend(model, effort):
        made.append((model, effort))
        return fake

    fake = _Fake()
    monkeypatch.setattr(turn, "_backend", backend)
    req = LlmRequest(model=OPUS, config=types.GenerateContentConfig(labels={"agent_effort": "max"}))
    assert _run(turn, req) == ["ok"]
    assert made == [(OPUS, "max")]
    assert fake.seen[0].config.labels is None


def test_turn_model_uses_thinking_level_for_native_gemini(monkeypatch):
    turn = models.TurnModel(model=HAIKU)
    fake = _Fake()
    monkeypatch.setattr(turn, "_backend", lambda model, effort: fake)
    req = LlmRequest(model=GEMINI, config=types.GenerateContentConfig(labels={"agent_effort": "medium"}))
    _run(turn, req)
    thinking = fake.seen[0].config.thinking_config
    assert thinking.thinking_level == types.ThinkingLevel.MEDIUM
    assert thinking.thinking_budget is None
    assert fake.seen[0].config.labels is None


def test_anthropic_gets_prompt_cache_marks_and_other_providers_dont():
    args = models.litellm_args(OPUS, "medium")
    assert args["reasoning_effort"] == "medium"
    points = args["cache_control_injection_points"]
    assert {"location": "message", "role": "system", "control": {"type": "ephemeral"}} in points
    assert {"location": "message", "index": -1, "control": {"type": "ephemeral"}} in points
    assert "cache_control_injection_points" not in models.litellm_args("openai/gpt-4o", None)
    assert models.litellm_args("openai/gpt-4o", None) == {}


def test_turn_model_builds_the_anthropic_backend_with_cache():
    backend = models.TurnModel(model=HAIKU)._backend(HAIKU, "low")
    assert backend._additional_args["cache_control_injection_points"]
