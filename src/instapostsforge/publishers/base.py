from __future__ import annotations

from abc import ABC, abstractmethod


class Publisher(ABC):
    @abstractmethod
    def publish_image(self, image_url: str, caption: str, alt_text: str | None = None) -> str:
        """Posts one image to the feed and returns the new media ID."""
