"""Temporary public hosting for the prepared JPEG, which Instagram downloads by URL.

Instagram rejects Google Drive links, so the day's photo is briefly hosted elsewhere.
Add another host by subclassing ImageHost and registering it here.
"""

from __future__ import annotations

from ..config import ConfigError
from .base import ImageHost


def build_host(settings: dict) -> ImageHost:
    kind = settings.get("type", "github")
    if kind == "github":
        from .github import GitHubHost

        return GitHubHost(settings)
    raise ConfigError(f"Unknown hosting type '{kind}'.")


__all__ = ["ImageHost", "build_host"]
