"""Caption writers. Add a new provider by subclassing CaptionWriter and registering it here."""

from __future__ import annotations

from ..config import ConfigError
from .base import CaptionWriter
from .validator import CaptionRules, normalize_caption


def build_caption_writer(settings: dict) -> CaptionWriter:
    provider = settings.get("provider", "claude")
    if provider == "claude":
        from .claude import ClaudeCaptionWriter

        return ClaudeCaptionWriter(settings)
    raise ConfigError(f"Unknown caption provider '{provider}'.")


__all__ = ["CaptionRules", "CaptionWriter", "build_caption_writer", "normalize_caption"]
