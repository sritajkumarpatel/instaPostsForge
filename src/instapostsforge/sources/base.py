"""The interface every photo source implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Photo


class PhotoSource(ABC):
    """Somewhere photos live: a Google Drive folder or a local folder."""

    @abstractmethod
    def list_photos(self) -> list[Photo]:
        """Returns every photo currently in the source."""

    @abstractmethod
    def download(self, photo: Photo) -> bytes:
        """Returns the original bytes of one photo."""
