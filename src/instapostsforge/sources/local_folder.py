"""Reads photos from a folder on the machine running the pipeline."""

from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from ..config import ConfigError
from ..models import Photo
from .base import PhotoSource

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}


class LocalFolderSource(PhotoSource):
    def __init__(self, path: str):
        self.root = Path(path).expanduser()
        if not self.root.is_dir():
            raise ConfigError(f"Photo folder not found: {self.root}")

    def list_photos(self) -> list[Photo]:
        photos = []
        for file in sorted(self.root.rglob("*")):
            if file.is_file() and file.suffix.lower() in IMAGE_EXTENSIONS:
                photos.append(
                    Photo(
                        id=str(file.relative_to(self.root)).replace("\\", "/"),
                        name=file.name,
                        created_at=datetime.fromtimestamp(file.stat().st_mtime, tz=timezone.utc),
                        mime_type=mimetypes.guess_type(file.name)[0],
                    )
                )
        return photos

    def download(self, photo: Photo) -> bytes:
        return (self.root / photo.id).read_bytes()
