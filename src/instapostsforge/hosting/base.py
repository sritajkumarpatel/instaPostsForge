from __future__ import annotations

from abc import ABC, abstractmethod


class ImageHost(ABC):
    @abstractmethod
    def upload(self, key: str, jpeg: bytes) -> str:
        """Stores the JPEG and returns a public HTTPS URL for it."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Removes the JPEG once Instagram has fetched it."""
