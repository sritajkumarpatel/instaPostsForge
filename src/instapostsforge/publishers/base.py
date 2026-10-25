from __future__ import annotations

from abc import ABC, abstractmethod


class Publisher(ABC):
    @abstractmethod
    def publish_image(self, image_url: str, caption: str, alt_text: str | None = None) -> str:
        """Posts one image to the feed and returns the new media ID."""

    def publish_carousel(self, image_urls: list[str], caption: str, alt_texts: list[str | None] | None = None) -> str:
        """Posts 2 to 10 images as one multi-photo post and returns its media ID."""
        raise NotImplementedError(f"{type(self).__name__} cannot publish multi-photo posts.")
