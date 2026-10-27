"""Choosing the AI provider: AI_PROVIDER over ai.provider, one model per provider, and an early key check."""

from pathlib import Path

import pytest

from instapostsforge.agents import build_workflow
from instapostsforge.agents.providers import PROVIDERS, model_provider, provider_name, resolve
from instapostsforge.config import ConfigError, load_profile

MODELS = {"claude": "claude-haiku-5-5", "openai": "gpt-5.6-luna", "gemini": "gemini-3.1-flash-lite"}


@pytest.fixture(autouse=True)
def no_ai_env(monkeypatch):
    for name in ("AI_PROVIDER", *(p.key_env for p in PROVIDERS.values())):
        monkeypatch.delenv(name, raising=False)


def test_config_decides_and_ai_provider_overrides(monkeypatch):
    assert provider_name({}) == "claude"
    assert provider_name({"provider": "gemini"}) == "gemini"
    monkeypatch.setenv("AI_PROVIDER", " 'OpenAI' ")
    assert provider_name({"provider": "gemini"}) == "openai"


def test_unknown_provider_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "mistral")
    with pytest.raises(ConfigError, match="Unknown AI provider 'mistral'. Use one of: claude, openai, gemini"):
        provider_name({})


def test_each_provider_gets_its_own_model(monkeypatch):
    for name, model in MODELS.items():
        monkeypatch.setenv("AI_PROVIDER", name)
        assert resolve({"models": MODELS})["model"] == model and model_provider(model) == name


def test_a_model_from_another_provider_is_refused(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    with pytest.raises(ConfigError, match="ai.agents.caption_writer.model is claude-opus-5-5, a Claude"):
        resolve({"models": MODELS, "agents": {"caption_writer": {"model": "claude-opus-5-5"}}})
    with pytest.raises(ConfigError, match="ai.model is claude-haiku-5-5"):
        resolve({"model": "claude-haiku-5-5", "models": MODELS})
    with pytest.raises(ConfigError, match="No model set for OpenAI"):
        resolve({"models": {"claude": "claude-haiku-5-5"}})


def test_missing_key_stops_before_any_ai_work(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-this-one")
    with pytest.raises(ConfigError, match="The AI provider is gemini, but GEMINI_API_KEY is empty"):
        build_workflow({"models": MODELS})


@pytest.mark.parametrize("name", list(MODELS))
def test_every_provider_builds_from_the_real_config(monkeypatch, name):
    monkeypatch.chdir(Path(__file__).parent.parent)
    monkeypatch.setenv("AI_PROVIDER", name)
    monkeypatch.setenv(PROVIDERS[name].key_env, "test-key")
    profile = load_profile("main")
    workflow = build_workflow(profile.ai)
    assert workflow.client.provider == name and workflow.model == MODELS[name]
    assert MODELS[name] in profile.ai["pricing"]  # so the token audit can price it


def test_forge_check_reports_the_provider_without_showing_the_error(monkeypatch):
    from instapostsforge import cli
    from instapostsforge.agents import clients

    class Client:
        provider = "openai"

        def __init__(self, fail):
            self.fail, self.checked = fail, []

        def check(self, model):
            self.checked.append(model)
            if self.fail:
                raise PermissionError("Incorrect API key provided: sk-abc***xyz")

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setattr(clients, "build_client", lambda provider: Client(False))
    assert cli.check_ai({"models": MODELS}) == "AI provider: OpenAI, OPENAI_API_KEY works, model gpt-5.6-luna"
    monkeypatch.setattr(clients, "build_client", lambda provider: Client(True))
    with pytest.raises(ConfigError) as err:
        cli.check_ai({"models": MODELS})
    assert "OpenAI rejected OPENAI_API_KEY or model gpt-5.6-luna: PermissionError" in str(err.value)
    assert "sk-abc" not in str(err.value)

    class APIConnectionError(Exception):
        pass

    class Offline(Client):
        def check(self, model):
            raise APIConnectionError("Connection error.")

    monkeypatch.setattr(clients, "build_client", lambda provider: Offline(False))
    with pytest.raises(ConfigError, match="Could not reach OpenAI"):
        cli.check_ai({"models": MODELS})
