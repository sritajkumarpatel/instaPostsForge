"""Temporary public hosting for the prepared JPEG, which Instagram downloads by URL."""

from __future__ import annotations

from ..config import ConfigError
from .base import ImageHost


def build_host(settings: dict) -> ImageHost:
    kind = settings.get("type", "s3")
    if kind == "s3":
        from .s3 import S3Host

        return S3Host(settings)
    raise ConfigError(f"Unknown hosting type '{kind}'.")


__all__ = ["ImageHost", "build_host"]
