"""One small client per model provider. The agents' prompts and schemas are shared."""

from __future__ import annotations

from .base import Image, ModelClient, ModelReply, Part, Text


def build_client(provider: str) -> ModelClient:
    from ..providers import require_key

    key = require_key(provider)
    if provider == "claude":
        from .anthropic import AnthropicClient

        return AnthropicClient(api_key=key)
    if provider == "openai":
        from .openai import OpenAIClient

        return OpenAIClient(api_key=key)
    if provider == "gemini":
        from .gemini import GeminiClient

        return GeminiClient(api_key=key)
    if provider == "ollama":
        from .ollama import OllamaClient

        return OllamaClient(api_key=key)
    from ...config import ConfigError

    raise ConfigError(f"Unknown AI provider '{provider}'.")


__all__ = ["Image", "ModelClient", "ModelReply", "Part", "Text", "build_client"]
