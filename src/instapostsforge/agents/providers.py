"""Which AI provider a run uses, its API key and its model.

ai.provider in config/profiles.yaml picks the provider; the AI_PROVIDER environment variable (a
repository variable in GitHub Actions) overrides it. ai.models names one model per provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..config import ConfigError


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    key_env: str
    model_prefixes: tuple[str, ...]


PROVIDERS = {
    "claude": Provider("claude", "Claude (Anthropic)", "ANTHROPIC_API_KEY", ("claude-",)),
    "openai": Provider("openai", "OpenAI", "OPENAI_API_KEY", ("gpt-", "chatgpt-", "o1", "o3", "o4", "ft:gpt-")),
    "gemini": Provider("gemini", "Gemini (Google)", "GEMINI_API_KEY", ("gemini-",)),
}


def provider_name(settings: dict) -> str:
    """AI_PROVIDER if set, else ai.provider, else claude."""
    name = os.environ.get("AI_PROVIDER", "").strip().strip("\"'").strip().lower() or str(settings.get("provider") or "claude").lower()
    if name not in PROVIDERS:
        raise ConfigError(f"Unknown AI provider '{name}'. Use one of: {', '.join(PROVIDERS)} (set by AI_PROVIDER or ai.provider).")
    return name


def model_provider(model: str) -> str | None:
    """The provider a model ID clearly belongs to, or None for an ID we don't recognise."""
    for provider in PROVIDERS.values():
        if model.lower().startswith(provider.model_prefixes):
            return provider.name
    return None


def resolve(settings: dict) -> dict:
    """The ai settings with provider and model filled in for this run, after checking they fit together."""
    name = provider_name(settings)
    model = settings.get("model") or (settings.get("models") or {}).get(name)
    if not model:
        raise ConfigError(f"No model set for {PROVIDERS[name].label}. Add one under ai.models.{name} in config/profiles.yaml.")
    resolved = {**settings, "provider": name, "model": model}
    used = [("ai.model" if settings.get("model") else f"ai.models.{name}", model)]
    used += [(f"ai.agents.{agent}.model", own["model"]) for agent, own in (settings.get("agents") or {}).items() if (own or {}).get("model")]
    for where, m in used:
        other = model_provider(m)
        if other and other != name:
            raise ConfigError(
                f"{where} is {m}, a {PROVIDERS[other].label} model, but the AI provider is {name}. "
                f"Change or remove it, or set AI_PROVIDER to {other}."
            )
    return resolved


def require_key(name: str) -> str:
    provider = PROVIDERS[name]
    key = os.environ.get(provider.key_env, "").strip()
    if not key:
        raise ConfigError(
            f"The AI provider is {name}, but {provider.key_env} is empty. Add it to .env, or as a repository secret "
            f"(Settings > Secrets and variables > Actions) for GitHub Actions."
        )
    return key
