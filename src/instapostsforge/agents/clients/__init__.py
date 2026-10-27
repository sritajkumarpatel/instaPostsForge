"""One small client per model provider. The agents' prompts and schemas are shared."""

from __future__ import annotations

from .base import Image, ModelClient, ModelReply, Part, Text


def build_client(provider: str) -> ModelClient:
    if provider == "claude":
        from .anthropic import AnthropicClient

        return AnthropicClient()
    from ...config import ConfigError

    raise ConfigError(f"Unknown AI provider '{provider}'.")


__all__ = ["Image", "ModelClient", "ModelReply", "Part", "Text", "build_client"]
