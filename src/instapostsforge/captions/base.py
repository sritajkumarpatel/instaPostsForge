"""The interface every caption writer implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Caption


class CaptionWriter(ABC):
    @abstractmethod
    def write(self, image_jpeg: bytes, *, photo_name: str, recent_captions: list[str]) -> Caption:
        """Looks at one photo and returns a caption, optional quote, hashtags and alt text."""
