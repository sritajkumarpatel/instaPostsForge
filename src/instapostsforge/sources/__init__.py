"""Photo sources. Add a new source by subclassing PhotoSource and registering it here."""

from __future__ import annotations

from ..config import ConfigError
from .base import PhotoSource


def build_source(settings: dict) -> PhotoSource:
    kind = settings.get("type")
    if kind == "google_drive":
        from .google_drive import GoogleDriveSource

        return GoogleDriveSource(settings)
    if kind == "local_folder":
        from .local_folder import LocalFolderSource

        return LocalFolderSource(settings["path"])
    raise ConfigError(f"Unknown source type '{kind}'. Use google_drive or local_folder.")


__all__ = ["PhotoSource", "build_source"]
