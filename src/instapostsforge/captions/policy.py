"""The content policy code enforces on every word that goes out: the caption, the quote, the hashtags
and the text on a styled image. The AI is asked to keep off these topics too, but this check holds
whatever the AI writes. The terms live in a plain text file (ai.blocked_terms_file) so the account
owner can edit them without touching code."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_FILE = "config/blocked-terms.txt"


@dataclass(frozen=True)
class ContentPolicy:
    terms: tuple[str, ...] = ()
    _patterns: tuple[tuple[str, re.Pattern], ...] = field(default=(), repr=False, compare=False)

    @classmethod
    def from_terms(cls, terms) -> "ContentPolicy":
        cleaned = tuple(dict.fromkeys(t.strip().lower() for t in terms if t.strip() and not t.strip().startswith("#")))
        patterns = []
        for term in cleaned:
            stem, wildcard = (term[:-1], True) if term.endswith("*") else (term, False)
            words = [re.escape(w) for w in re.split(r"[\s-]+", stem) if w]
            if not words:
                continue
            body = r"[\s-]+".join(words)  # "war zone", "war-zone" and "war  zone" all match
            patterns.append((term, re.compile(rf"(?<![\w]){body}{r'[\w]*' if wildcard else ''}(?![\w])", re.IGNORECASE)))
        return cls(cleaned, tuple(patterns))

    @classmethod
    def from_settings(cls, settings: dict) -> "ContentPolicy":
        """ai.blocked_terms_file, defaulting to config/blocked-terms.txt. A missing file means no list,
        with a warning, so a copy of the repo without it still posts."""
        path = settings.get("blocked_terms_file", DEFAULT_FILE)
        if not path:
            return cls()
        file = Path(path)
        if not file.exists():
            log.warning("Blocked-terms file %s not found; posting without the word check.", path)
            return cls()
        return cls.from_terms(file.read_text(encoding="utf-8").splitlines())

    def found_in(self, text: str | None) -> list[str]:
        """The blocked terms that appear in the text, as listed in the file."""
        if not text:
            return []
        return [term for term, pattern in self._patterns if pattern.search(text)]

    def blocks_tag(self, tag: str) -> bool:
        """A hashtag is one run of letters, so a term matches the whole tag, and a term ending in *
        also matches the start of one (#electionday). Nothing looser: #brahminykite is a bird."""
        body = tag.lower().lstrip("#")
        for term, _ in self._patterns:
            stem = re.sub(r"[\s*-]+", "", term)
            if not stem:
                continue
            if body == stem or (term.endswith("*") and body.startswith(stem)):
                return True
        return False
