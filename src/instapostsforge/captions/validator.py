"""Checks and tidies a caption so it follows Instagram's rules before posting."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Caption

# Tags that are so broad they add nothing (Instagram has said as much).
GENERIC_TAGS = {
    "#explore", "#explorepage", "#viral", "#trending", "#fyp", "#foryou", "#foryoupage",
    "#instagood", "#instadaily", "#reels", "#like4like", "#follow4follow", "#followforfollow",
    "#photooftheday", "#picoftheday", "#love", "#instagram", "#instalike", "#likeforlikes", "#followme",
}
TAG_PATTERN = re.compile(r"^#[\w]+$", re.UNICODE)
SENTENCE_BREAK = re.compile(r"(?<=[.!?…])(\s+)")


class CaptionError(Exception):
    """Raised when a caption cannot be made valid."""


@dataclass(frozen=True)
class CaptionRules:
    min_hashtags: int = 3
    max_hashtags: int = 5
    max_chars: int = 2200
    max_text_chars: int = 150  # the caption text alone: hook, body and ending, without quote or tags

    @classmethod
    def from_settings(cls, settings: dict) -> "CaptionRules":
        tags = settings.get("hashtags") or {}
        return cls(
            min_hashtags=int(tags.get("min", 3)),
            max_hashtags=int(tags.get("max", 5)),
            max_chars=int(settings.get("max_caption_chars", 2200)),
            max_text_chars=int(settings.get("max_caption_text_chars", 150)),
        )


def clean_hashtag(tag: str) -> str | None:
    tag = "#" + re.sub(r"\s+", "", tag.strip().lstrip("#"))
    tag = tag.lower()
    if not TAG_PATTERN.match(tag) or tag in GENERIC_TAGS or len(tag) > 31:
        return None
    return tag


def trim_caption_text(text: str, limit: int) -> str | None:
    """Shortens caption text to at most limit characters by dropping whole sentences: body sentences
    first, from the last one back, then the closing question. The first sentence (the hook) always
    stays. Returns None when the hook alone is too long, so the writer has to shorten it."""
    text = text.strip()
    if len(text) <= limit:
        return text
    parts = SENTENCE_BREAK.split(text)
    sentences, breaks = parts[::2], [*parts[1::2], ""]
    last = len(sentences) - 1
    middle = list(range(1, last)) if sentences[last].rstrip().endswith("?") else list(range(1, last + 1))
    order = [*reversed(middle), *([last] if last and last not in middle else [])]
    kept = list(range(len(sentences)))

    def joined() -> str:
        return "".join(sentences[i] + (breaks[i] if n < len(kept) - 1 else "") for n, i in enumerate(kept)).strip()

    for i in order:
        if len(joined()) <= limit:
            break
        kept.remove(i)
    result = joined()
    return result if len(result) <= limit else None


def normalize_caption(caption: Caption, rules: CaptionRules) -> Caption:
    """Returns a cleaned copy of the caption, or raises CaptionError."""
    tags: list[str] = []
    for raw in caption.hashtags:
        tag = clean_hashtag(raw)
        if tag and tag not in tags:
            tags.append(tag)
    tags = tags[: rules.max_hashtags]
    if len(tags) < rules.min_hashtags:
        raise CaptionError(f"Only {len(tags)} usable hashtags; need at least {rules.min_hashtags}.")

    text = caption.text.strip()
    if "#" in text:  # keep hashtags in one place so the count stays within the limit
        text = re.sub(r"\s*#\w+", "", text).strip()
    if not text:
        raise CaptionError("Caption text is empty.")

    cleaned = Caption(
        text=text,
        hashtags=tags,
        alt_text=caption.alt_text.strip()[:1000],
        quote=(caption.quote or "").strip() or None,
        quote_author=(caption.quote_author or "").strip() or None,
        more_alt_texts=[a.strip()[:1000] for a in caption.more_alt_texts],
    )
    if len(cleaned.render()) > rules.max_chars:
        raise CaptionError(f"Caption is {len(cleaned.render())} characters; the limit is {rules.max_chars}.")
    return cleaned
