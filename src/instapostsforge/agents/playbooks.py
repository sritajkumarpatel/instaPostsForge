"""Fixed photo categories and moods, and the playbook section each AI step gets for them.

The analyst labels every photo with one category and one mood from the lists below. The
caption writer, hashtag strategist and reviewer then receive only the playbook sections for
that category and mood (config/playbooks/*.md), instead of one general set of rules.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

CATEGORIES = (
    "landscape",
    "sky",
    "city",
    "street",
    "people",
    "food",
    "animals",
    "macro",
    "night",
    "travel",
    "event",
    "abstract",
)
MOODS = ("calm", "joyful", "energetic", "nostalgic", "dramatic", "romantic", "playful", "awe")

# With quote: when_fitting, a quote on these categories is dropped in code, whatever the model wrote.
NO_QUOTE_CATEGORIES = frozenset({"food", "animals"})

_HEADING = re.compile(r"^## +([a-z_]+)\s*$", re.MULTILINE)


def parse_sections(text: str) -> dict[str, str]:
    """Splits a playbook file into {key: body} on '## key' headings."""
    matches = list(_HEADING.finditer(text))
    sections = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[match.group(1)] = text[match.end() : end].strip()
    return sections


class Playbooks:
    def __init__(self, categories: dict[str, str], moods: dict[str, str]):
        self.categories = categories
        self.moods = moods

    @classmethod
    def load(cls, directory: str | Path | None) -> "Playbooks":
        if not directory:
            return cls({}, {})
        folder = Path(directory)
        found = {}
        for name in ("categories", "moods"):
            path = folder / f"{name}.md"
            if path.exists():
                found[name] = parse_sections(path.read_text(encoding="utf-8"))
            else:
                log.warning("Playbook %s not found; that step will use its general rules only.", path)
                found[name] = {}
        return cls(found["categories"], found["moods"])

    def for_photo(self, analysis: dict) -> str:
        """The playbook text for one photo's category and mood, ready to put in a prompt."""
        parts = []
        category, mood = analysis.get("category"), analysis.get("mood")
        if category in self.categories:
            parts.append(f"## Category: {category}\n\n{self.categories[category]}")
        if mood in self.moods:
            parts.append(f"## Mood: {mood}\n\n{self.moods[mood]}")
        if not parts:
            return "(No playbook for this photo; follow the general rules.)"
        return "\n\n".join(parts)
