"""Validación de la configuración: lo obligatorio frena el arranque, y prod pide más."""

import pytest

from agent_dashboards import config

FULL = {
    "WAREHOUSE_PROJECT": "proj",
    "GOOGLE_CLIENT_ID": "id",
    "GOOGLE_CLIENT_SECRET": "secreto",
    "ADMIN_EMAILS": {"hola@empresa.com"},
    "QUERY_CREDENTIALS": "reader",
    "AGENT_MODEL": "anthropic/claude-haiku-5-5",
    "PUBLIC_URL": "",
    "SECRET_KEY": "",
}


@pytest.fixture
def full(monkeypatch):
    for k, v in FULL.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(config, "ALLOWED_EMAIL_DOMAINS", set())
    monkeypatch.setattr(config, "ALLOWED_EMAILS", set())
    monkeypatch.setattr(config, "AGENT_MODELS", [])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-prueba")
    monkeypatch.setattr(config, "APP_ENV", "local")
    return monkeypatch


def test_local_with_the_essentials_starts(full):
    assert config.problems() == []
    config.validate()


def test_missing_required_values_are_listed(full):
    full.setattr(config, "WAREHOUSE_PROJECT", "")
    full.setattr(config, "GOOGLE_CLIENT_SECRET", "")
    full.setattr(config, "ADMIN_EMAILS", set())
    full.delenv("ANTHROPIC_API_KEY")
    found = " ".join(config.problems())
    for name in ("WAREHOUSE_PROJECT", "GOOGLE_CLIENT_SECRET", "ADMIN_EMAILS", "ANTHROPIC_API_KEY"):
        assert name in found
    with pytest.raises(config.ConfigError, match="APP_ENV=local"):
        config.validate()


def test_other_model_providers_do_not_need_the_anthropic_key(full):
    full.delenv("ANTHROPIC_API_KEY")
    full.setattr(config, "AGENT_MODEL", "openai/gpt-x")
    full.setenv("OPENAI_API_KEY", "clave")
    assert config.problems() == []


def test_gemini_with_litellm_needs_its_key(full):
    full.delenv("ANTHROPIC_API_KEY")
    full.setattr(config, "AGENT_MODEL", "gemini/gemini-3.8-flash")
    full.delenv("GEMINI_API_KEY", raising=False)
    assert "GEMINI_API_KEY" in " ".join(config.problems())
    full.setenv("GEMINI_API_KEY", "clave-prueba")
    assert config.problems() == []


def test_every_selectable_model_needs_its_provider_key(full):
    full.setattr(config, "AGENT_MODELS", [("openai/gpt-x", "GPT"), ("gemini-3.8-flash", "Gemini")])
    for key in ("OPENAI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
        full.delenv(key, raising=False)
    found = " ".join(config.problems())
    assert "OPENAI_API_KEY" in found and "GOOGLE_API_KEY" in found
    full.setenv("OPENAI_API_KEY", "clave")
    full.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    assert config.problems() == []


def test_model_list_keeps_order_labels_and_no_duplicates():
    assert config._model_list(" anthropic/claude-opus-5-5=Opus 5.5, openai/gpt-x ,anthropic/claude-opus-5-5=Otro") == [
        ("anthropic/claude-opus-5-5", "Opus 5.5"),
        ("openai/gpt-x", "gpt-x"),
    ]


def test_prod_requires_https_url_and_secret_key(full):
    full.setattr(config, "APP_ENV", "prod")
    found = " ".join(config.problems())
    assert "PUBLIC_URL" in found and "SECRET_KEY" in found
    full.setattr(config, "PUBLIC_URL", "http://bi.empresa.com")
    full.setattr(config, "SECRET_KEY", "x" * 48)
    assert [p for p in config.problems() if "PUBLIC_URL" in p]
    full.setattr(config, "PUBLIC_URL", "https://bi.empresa.com")
    assert config.problems() == []


def test_relative_paths_are_relative_to_the_project(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "otra/data")
    assert config._path("DATA_DIR", config.PACKAGE_DIR / "data") == (config.PACKAGE_DIR / "otra" / "data").resolve()
    monkeypatch.delenv("DATA_DIR")
    assert config._path("DATA_DIR", config.PACKAGE_DIR / "data") == (config.PACKAGE_DIR / "data").resolve()
