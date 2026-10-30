"""Photo sources. Add a new source by subclassing PhotoSource and registering it here."""

from __future__ import annotations

import os

from ..config import ConfigError
from .base import PhotoSource

SOURCES = ("google_drive", "r2", "local_folder")


def source_type(settings: dict) -> str:
    """PHOTO_SOURCE if set, else source.type. Each source keeps its own photos, so switching is a fresh start."""
    kind = os.environ.get("PHOTO_SOURCE", "").strip().strip("\"'").strip().lower() or str(settings.get("type") or "").lower()
    if kind not in SOURCES:
        raise ConfigError(f"Unknown photo source '{kind}'. Use google_drive or r2 (set by PHOTO_SOURCE or source.type).")
    return kind


def build_source(settings: dict) -> PhotoSource:
    kind = source_type(settings)
    if kind == "google_drive":
        from .google_drive import GoogleDriveSource

        return GoogleDriveSource(settings)
    if kind == "r2":
        from .r2 import R2Source

        if not settings.get("r2"):
            raise ConfigError("The photo source is r2, but the profile has no source.r2 block in config/profiles.yaml.")
        return R2Source(settings["r2"])
    from .local_folder import LocalFolderSource

    return LocalFolderSource(settings["path"])


__all__ = ["PhotoSource", "build_source", "source_type"]
