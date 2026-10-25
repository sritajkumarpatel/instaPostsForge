"""Plain data types passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Photo:
    """A photo available in a source, before it is downloaded."""

    id: str
    name: str
    created_at: datetime | None = None  # when it was added to the source, not when it was taken
    mime_type: str | None = None
    taken_at: datetime | None = None  # capture time from the source's photo metadata, when it has one
    # GPS (latitude, longitude) from the source's photo metadata. Used only during a run and never
    # written to state/, because the repo is public.
    location: tuple[float, float] | None = None


@dataclass
class Caption:
    """What the caption writer produced for one photo."""

    text: str
    hashtags: list[str]
    alt_text: str
    quote: str | None = None
    quote_author: str | None = None
    more_alt_texts: list[str] = field(default_factory=list)  # one per extra photo of a multi-photo post

    def render(self) -> str:
        """The full caption as it appears on Instagram."""
        parts = [self.text.strip()]
        if self.quote:
            line = f"“{self.quote.strip()}”"
            if self.quote_author:
                line += f" — {self.quote_author.strip()}"
            parts.append(line)
        if self.hashtags:
            parts.append(" ".join(self.hashtags))
        return "\n\n".join(parts)


@dataclass
class PostResult:
    """Outcome of one pipeline run."""

    profile: str
    photo: Photo  # the photo posted, or the cover of a multi-photo post
    caption: Caption
    published: bool
    media_id: str | None = None
    draft_dir: str | None = None
    plan_reason: str = ""
    usage: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    more_photos: list[Photo] = field(default_factory=list)  # the other photos of a multi-photo post

    @property
    def photos(self) -> list[Photo]:
        return [self.photo, *self.more_photos]
