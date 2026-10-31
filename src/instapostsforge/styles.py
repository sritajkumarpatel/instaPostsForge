"""Post styles: how a day's photo or photos are laid out before they go to Instagram.

"single" is the plain photo as shot, exactly as before styles existed, and it stays the default. The
other styles are drawn by code (imaging/layouts.py) from templates:

- grid: 2 to 9 photos of one outing or place tiled on one slide, then each photo on its own slide.
- gallery: one photo on a wide margin with a small museum-style label (title, place, month).
- poster: one photo as a print on warm paper with a spaced title, a quiet line and a handwritten note.
- panorama: one wide photo cut into 2 or 3 slides that join up as you swipe.
- story: a title slide over the cover, the other photos on gallery margins, and a closing line.
- auto: code picks one of the styles in selection.style.auto_styles that suits the day's photos.

The AI's part is choosing the photos (the planner, as for multi-photo posts) and writing the few
words that go on the image (the caption writer's overlay_* fields). Code checks those words like the
caption, and a style that can't be drawn falls back to a simpler one, never to a broken post.

The choice comes from selection.style in config/profiles.yaml, overridden for a run by the POST_STYLE,
POST_MOOD and POST_CATEGORY environment variables (the Run workflow's choices).
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from datetime import date

from .agents.playbooks import CATEGORIES, MOODS

log = logging.getLogger(__name__)

STYLES = ("single", "auto", "grid", "gallery", "poster", "panorama", "story")
# Styles that put several photos in one post, and the most photos each may hold. A carousel has at
# most 10 slides: a grid adds its collage slide, and a story adds a closing slide.
MULTI_STYLES = {"grid": 9, "story": 9}
GRID_SIZES = (9, 6, 4, 3, 2)  # how many photos a grid can hold; others are trimmed to the next size down
PANORAMA_MIN_SHAPE = 1.5  # width:height a photo needs to make at least two joined slides
OFF = {"", "any", "all", "auto", "none", "variable"}

# For auto: the styles that suit each mood, best first. Plain "single" suits every mood.
MOOD_STYLES = {
    "calm": ("gallery", "poster"),
    "nostalgic": ("poster", "gallery"),
    "romantic": ("poster", "gallery"),
    "awe": ("panorama", "gallery"),
    "dramatic": ("gallery", "panorama"),
    "joyful": ("gallery", "poster"),
    "energetic": ("gallery",),
    "playful": ("gallery",),
}

# What the caption writer is told about the text on the image, per style.
BRIEFS = {
    "grid": (
        "Today's post is a photo grid: all the photos tiled on one slide, then each on its own. "
        "overlay_title is one short line set in large type across the middle of the grid, 2 to 5 words "
        "and at most 32 characters, naming what ties the photos together (for example \"Mysuru in blue\" or "
        "\"One slow Sunday\"). Leave overlay_line and overlay_note empty."
    ),
    "gallery": (
        "Today's photo is shown on a wide gallery margin with a small museum-style label under it. "
        "overlay_title is the title on that label: 1 to 4 words, at most 32 characters, like the title of a print "
        "(for example \"After the rain\"). Code adds the place and month under it. Leave overlay_line and overlay_note empty."
    ),
    "poster": (
        "Today's photo is set as a print on warm paper, like a poster. Write three short pieces of text for it:\n"
        "- overlay_title: two short contrasting phrases separated by a comma, at most 32 characters, set in spaced "
        "capitals (for example \"Small towns, big dreams\" or \"Old walls, new light\").\n"
        "- overlay_line: one quiet sentence under it, at most 80 characters (for example \"Not just a delivery, "
        "but a little part of someone's tomorrow.\").\n"
        "- overlay_note: a handwritten aside in the margin, 2 to 5 words, at most 32 characters (for example "
        "\"Same roads, different stories\")."
    ),
    "story": (
        "Today's post is a short story in slides: the cover with a title over it, the other photos, then a closing slide. "
        "overlay_title is the title on the cover, 2 to 5 words, at most 32 characters. overlay_line is the one sentence "
        "on the closing slide, at most 80 characters, that ends the story. Leave overlay_note empty."
    ),
}
NO_TEXT_BRIEF = "Nothing is written on the image today: leave overlay_title, overlay_line and overlay_note empty."
TEXT_RULES = (
    "Text on the image can't be edited after posting, so it follows every rule the caption does, and more strictly: "
    "describe only what the photos show; no names of people, brands or places that aren't in place_name; no hashtags, "
    "mentions, emoji, quotes or questions; nothing about the topics this account never touches. Make it different "
    "from the caption's hook, not a copy of it. For a deity, temple or festival, keep it reverent."
)


def brief_for(style: str) -> str:
    """What the caption writer is told about text on the image; empty for a plain photo."""
    if style not in BRIEFS:
        return ""
    return f"{BRIEFS[style]}\n\n{TEXT_RULES}"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip().strip("\"'").strip().lower()


@dataclass
class StyleRequest:
    """Today's requested style and photo filters, from config and the run's environment."""

    name: str = "single"
    mood: str | None = None
    category: str | None = None
    auto_styles: list[str] = field(default_factory=lambda: ["single", "gallery", "poster", "grid", "story", "panorama"])
    decor_dir: str | None = None
    switched_by: str = "selection.style in the config"

    @property
    def plain(self) -> bool:
        return self.name == "single"

    @property
    def wants_set(self) -> bool:
        """Whether the planner should be asked for several photos whatever the multi-photo switch says."""
        return self.name in MULTI_STYLES

    @property
    def single_only(self) -> bool:
        """Whether today's style holds exactly one photo, so the planner shouldn't offer a set."""
        return self.name in ("gallery", "poster", "panorama")


def _pick(kind: str, known: tuple[str, ...], *values) -> str | None:
    """The first of the run's choice and the config value that is a known mood or category."""
    for value in values:
        value = str(value or "").strip().lower()
        if not value or value in OFF:
            continue
        if value in known:
            return value
        log.warning("Unknown %s %r ignored. Choose one of: %s.", kind, value, ", ".join(known))
    return None


def style_request(selection: dict) -> StyleRequest:
    """selection.style, with POST_STYLE, POST_MOOD and POST_CATEGORY overriding it for one run. An
    unknown value is ignored with a warning, never an error, so a typo can't stop the daily post."""
    settings = dict(selection.get("style") or {})
    request = StyleRequest(decor_dir=settings.get("decor_dir"))
    if settings.get("auto_styles"):
        request.auto_styles = [s for s in settings["auto_styles"] if s in STYLES and s != "auto"] or ["single"]

    name = str(settings.get("name", "single")).strip().lower()
    if (env := _env("POST_STYLE")) and env not in OFF - {"auto"}:
        name, request.switched_by = env, f"POST_STYLE={env}"
    if name not in STYLES:
        log.warning("Unknown post style %r; posting a plain photo. Choose one of: %s.", name, ", ".join(STYLES))
        name = "single"
    request.name = name

    request.mood = _pick("mood", MOODS, _env("POST_MOOD"), settings.get("mood"))
    request.category = _pick("category", CATEGORIES, _env("POST_CATEGORY"), settings.get("category"))
    return request


def grid_size(count: int) -> int:
    return next((n for n in GRID_SIZES if n <= count), 0)


def choose_style(request: StyleRequest, *, photo_count: int, cover: dict, today: date, last_style: str | None = None) -> tuple[str, str | None]:
    """The style today's post is drawn in, given the photos the planner chose, and a note when the
    requested style couldn't be used. Falls back step by step: a set style with one photo becomes a
    single-photo style, and a panorama of a photo that isn't wide becomes a gallery frame."""
    shape = cover.get("shape") or 1.0
    wide = shape >= PANORAMA_MIN_SHAPE
    name = request.name
    if name == "auto":
        allowed = request.auto_styles
        if photo_count > 1:
            options = [s for s in ("grid", "story") if s in allowed and (s != "grid" or photo_count >= 3)]
            options = options or (["single"] if "single" in allowed else [])
        else:
            suits = MOOD_STYLES.get(cover.get("mood") or "", ("gallery",))
            options = [s for s in (*suits, "single") if s in allowed and (s != "panorama" or wide)]
        options = [s for s in options if s != last_style] or options  # a different look from yesterday's when possible
        return (random.Random(today.isoformat()).choice(options) if options else "single"), None
    if name in MULTI_STYLES and photo_count < 2:
        fallback = "gallery" if name == "grid" else "poster"
        return fallback, f"The {name} style needs several photos from one outing or place, so today's photo was set as a {fallback} instead."
    if name == "panorama" and not wide:
        return "gallery", "The photo isn't wide enough to split into a panorama, so it was set in a gallery frame instead."
    return name, None
