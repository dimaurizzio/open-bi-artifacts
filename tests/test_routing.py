from types import SimpleNamespace

import pytest

from agent_dashboards import config, routing

BASE = "anthropic/claude-haiku-5-5"
STRONG = "anthropic/claude-sonnet-5-5"


@pytest.fixture(autouse=True)
def routes(monkeypatch):
    monkeypatch.setattr(config, "AGENT_MODEL", BASE)
    monkeypatch.setattr(config, "MODEL_ROUTES", {"dashboard-builder": STRONG})


def ctx(state, invocation):
    return SimpleNamespace(state=state, invocation_id=invocation)


def load(state, invocation, skill, response=None):
    tool = SimpleNamespace(name="load_skill")
    routing.track_skill_route(tool, {"skill_name": skill}, ctx(state, invocation), response or {"instructions": "..."})


def model_for(state, invocation):
    request = SimpleNamespace(model=BASE)
    routing.route_model(ctx(state, invocation), request)
    return request.model


def test_base_model_without_routed_skill():
    state = {}
    load(state, "inv-1", "bigquery-warehouse")
    assert model_for(state, "inv-1") == BASE


def test_routed_skill_switches_model_for_the_turn():
    state = {}
    assert model_for(state, "inv-1") == BASE
    load(state, "inv-1", "dashboard-builder")
    assert model_for(state, "inv-1") == STRONG


def test_next_turn_returns_to_base_model():
    state = {}
    load(state, "inv-1", "dashboard-builder")
    assert model_for(state, "inv-2") == BASE


def test_failed_load_does_not_switch():
    state = {}
    load(state, "inv-1", "dashboard-builder", response={"error": "Skill not found"})
    assert model_for(state, "inv-1") == BASE


def test_other_tools_are_ignored():
    state = {}
    routing.track_skill_route(SimpleNamespace(name="artifact_write"), {"skill_name": "dashboard-builder"}, ctx(state, "inv-1"), {})
    assert model_for(state, "inv-1") == BASE
