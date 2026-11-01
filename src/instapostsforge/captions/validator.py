"""Checks and tidies a caption so it follows Instagram's rules before posting."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Caption
from .policy import ContentPolicy

# Tags that are so broad they add nothing (Instagram has said as much).
GENERIC_TAGS = {
    "#explore", "#explorepage", "#viral", "#trending", "#fyp", "#foryou", "#foryoupage",
    "#instagood", "#instadaily", "#reels", "#like4like", "#follow4follow", "#followforfollow",
    "#photooftheday", "#picoftheday", "#love", "#instagram", "#instalike", "#likeforlikes", "#followme",
}
TAG_PATTERN = re.compile(r"^#[\w]+$", re.UNICODE)
SENTENCE_BREAK = re.compile(r"(?<=[.!?…])(\s+)")
# The most characters each piece of text on a styled image may have; longer ones are left off the image.
OVERLAY_LIMITS = {"title": 32, "line": 80, "note": 32}
# Text on an image is drawn with the bundled fonts: letters, digits and plain punctuation only.
OVERLAY_ALLOWED = re.compile(r"[^\w\s.,'’‘\-–—:;!?&()]", re.UNICODE)


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


def clean_overlay(overlay: dict[str, str] | None) -> dict[str, str]:
    """The text for a styled image, tidied: no emoji, hashtags, mentions or links (the fonts can't draw
    emoji, and nothing on an image can be clicked), and each piece within its length or left off."""
    cleaned: dict[str, str] = {}
    for key, limit in OVERLAY_LIMITS.items():
        text = (overlay or {}).get(key) or ""
        text = re.sub(r"https?://\S+|[#@]\w+", "", text)
        text = " ".join(OVERLAY_ALLOWED.sub("", text).split()).strip(" -–—:;,")
        if text and len(text) <= limit:
            cleaned[key] = text
    return cleaned


def normalize_caption(caption: Caption, rules: CaptionRules, policy: ContentPolicy | None = None) -> Caption:
    """Returns a cleaned copy of the caption, or raises CaptionError. With a content policy, a hashtag on
    its list is dropped, and a blocked term anywhere in the words raises, so the writer tries again."""
    tags: list[str] = []
    for raw in caption.hashtags:
        tag = clean_hashtag(raw)
        if tag and policy is not None and policy.blocks_tag(tag):
            continue
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
        overlay=clean_overlay(caption.overlay),
    )
    if policy is not None:
        words = [cleaned.text, cleaned.quote, cleaned.quote_author, cleaned.alt_text, *cleaned.more_alt_texts, *cleaned.overlay.values()]
        found = sorted({term for w in words for term in policy.found_in(w)})
        if found:
            raise CaptionError(
                f"content: the post mentions {', '.join(repr(t) for t in found)}, a topic this account never posts about "
                "(politics, caste, religious debate, war and conflict, sexual content). Rewrite it without that topic."
            )
    if len(cleaned.render()) > rules.max_chars:
        raise CaptionError(f"Caption is {len(cleaned.render())} characters; the limit is {rules.max_chars}.")
    return cleaned
