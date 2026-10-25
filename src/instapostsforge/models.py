"""Plain data types passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Photo:
    """A photo available in a source, before it is downloaded."""

    id: str
    name: str
    created_at: datetime | None = None
    mime_type: str | None = None


@dataclass
class Caption:
    """What the caption writer produced for one photo."""

    text: str
    hashtags: list[str]
    alt_text: str
    quote: str | None = None
    quote_author: str | None = None

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
    photo: Photo
    caption: Caption
    published: bool
    media_id: str | None = None
    draft_dir: str | None = None
    plan_reason: str = ""
    notes: list[str] = field(default_factory=list)
