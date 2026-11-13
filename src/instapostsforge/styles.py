"""Post styles: how a day's photo or photos are laid out before they go to Instagram.

"any" is the default: every post is drawn in one of the designed styles below, and the AI planner
chooses which one suits each day's photos. "single" is the plain photo as shot, used only when chosen.
The designed styles are drawn by code (imaging/layouts.py, imaging/carousels.py, imaging/frames.py)
from templates, and most of them open on the photo itself and use the swipe to show something new:

- palette: the photo, then a card with the five colours it is made of, named by code.
- details: the photo, then two or three close crops on the things the analyst pointed at, each with
  a small map of where it sits and the writer's short label.
- film: one photo on a strip of film with its frame number and a short title.
- panorama: one wide photo cut into 2 or 3 slides that join up as you swipe.
- notes: one image, the photo with a hand-drawn layer on top (notes with arrows, a pen ring or rays,
  doodles, a star map, a film date stamp, viewfinder marks and more). The photo's pixels are unchanged.
- popout: an editorial poster on paper: the photo shows through a window, and its main subject steps
  out of the window onto the page (cut out by a local model in imaging/cutout.py, never repainted),
  under a large serif title and widely spaced small lines.
- grid: 2 to 9 photos of one outing or place tiled on one slide, then each photo on its own slide.
- story: a title slide over the cover, the other photos, and a closing line.
- contact_sheet: the chosen photo, then the whole set on a film contact sheet with the chosen frame
  circled in red grease pencil, then the others.
- seamless: 2 to 4 photos across one wide canvas cut into slides, joined by one hand-drawn line.
- sequence: 3 to 8 photos of one scene minutes apart, each with the time it was taken.
- any: the planner picks one of selection.style.any_styles for each post from the photos' notes; if
  its pick can't be drawn, code picks one that suits the photos' mood. "auto" means the same.

The poster, gallery frame, postcard, ticket, magazine and festive frames were retired in November
2026: they were small variations of one look (a photo on a margin with a label) and made the grid
repetitive. A config or run that still names "poster" or "gallery" gets "any" with a warning.

The AI's part is choosing the photos (the planner, as for multi-photo posts) and writing the few
words that go on the image (the caption writer's overlay_* fields). Code checks those words like the
caption, and a style that can't be drawn falls back to a simpler one, never to a broken post. Code also
keeps the feed varied: a style used by either of the last two posts is avoided when another fits.

On top of the style, code picks a "look" by plain rules (choose_look): the paper behind a pop-out,
polaroids for a small grid, or the marks drawn for a notes post. The look follows the photo's mood and
category, never repeats yesterday's, and changes with the date. No AI call is spent on it.

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

STYLES = (
    "any", "single", "palette", "details", "film", "panorama", "notes", "popout",
    "grid", "story", "contact_sheet", "seamless", "sequence",
)
DESIGNED = tuple(s for s in STYLES if s not in ("any", "single"))  # what "any" chooses from by default
ALIASES = {"auto": "any"}
RETIRED = {"poster", "gallery"}  # styles that no longer exist; a config naming them gets "any"
# Styles that put several photos in one post: the most photos each may hold (a carousel has at most
# 10 slides: a grid, contact sheet or story adds one slide of its own), and the fewest it needs.
MULTI_STYLES = {"grid": 9, "story": 9, "contact_sheet": 9, "seamless": 4, "sequence": 8}
SET_MINIMUM = {"grid": 3, "story": 2, "contact_sheet": 3, "seamless": 2, "sequence": 3}
SINGLE_STYLES = ("palette", "details", "film", "panorama", "notes", "popout")  # hold exactly one photo
GRID_SIZES = (9, 6, 4, 3, 2)  # how many photos a grid can hold; others are trimmed to the next size down
PANORAMA_MIN_SHAPE = 1.5  # width:height a photo needs to make at least two joined slides
OFF = {"", "any", "all", "auto", "none", "variable"}  # "no filter" for mood and category
FALLBACK = "palette"  # drawable for any photo, so every style that can't be drawn ends here

# Short descriptions the planner chooses from when the style is "any".
STYLE_GUIDE = {
    "palette": "the photo, then a card of the five colours it is made of; suits photos where colour is the story: sunsets, flowers, food, painted walls, markets",
    "details": "the photo, then close crops on two or three things in it, each labelled; suits busy scenes with distinct things to look at: food, markets, street corners, architecture, temples, macro",
    "film": "one photo on a strip of film with a frame number and a short title; suits candid street, night and travel moments",
    "panorama": "one wide photo (3:2 or wider, shape 1.5 or more) split into joined slides; suits wide landscapes, skylines and seascapes",
    "notes": "the photo with a few hand-drawn notes, arrows or doodles on top, as one image; suits photos with two or three distinct things to point at, food, street and travel moments, and night skies; never for photos of a deity, shrine or prayer",
    "popout": "an editorial poster: the main subject steps out of the photo's frame onto paper under a big title; only for one clear, sharp subject (a person, vehicle, animal or object) with space around it, never a deity, shrine or prayer, a crowd, a flat scene or a pure landscape",
    "grid": "3 to 9 photos from one set tiled on one slide, then each on its own; suits outings with many varied photos",
    "story": "2 to 9 photos from one set as a sequence with a title and a closing line; suits outings that unfold over time",
    "contact_sheet": "3 to 9 photos from one set: the best one, then all of them on a film contact sheet with the best circled in red pencil, then the rest; suits a walk or shoot where you worked one place from many angles",
    "seamless": "2 to 4 photos from one set laid across joined slides with one hand-drawn line running through them; suits a journey or a walk from one place to the next",
    "sequence": "3 to 8 photos of the same scene taken minutes apart, shown in time order with the time on each; suits light changing: a sunset, a sky, a festival evening, a street filling up",
    "single": "the photo as shot, with no layout",
}

# For any, when the planner's pick can't be used: the single-photo styles that suit each mood, best first.
MOOD_STYLES = {
    "calm": ("palette", "details"),
    "nostalgic": ("film", "palette"),
    "romantic": ("palette", "film"),
    "awe": ("panorama", "palette"),
    "dramatic": ("film", "panorama", "palette"),
    "joyful": ("details", "palette"),
    "energetic": ("details", "film"),
    "playful": ("details", "notes"),
}

NOTES_CATEGORIES = ("food", "street", "travel", "night", "macro")  # where code's own fallback may pick notes

# What the caption writer is told about the text on the image, per style.
BRIEFS = {
    "grid": (
        "Today's post is a photo grid: all the photos tiled on one slide, then each on its own. "
        "overlay_title is one short line set in large type across the middle of the grid, 2 to 5 words "
        "and at most 32 characters, naming what ties the photos together (for example \"Mysuru in blue\" or "
        "\"One slow Sunday\"). Leave overlay_line and overlay_note empty."
    ),
    "palette": (
        "Today's post is the photo, then a card showing the five colours it is made of; code names the colours. "
        "overlay_title is the title above the card: 1 to 4 words, at most 32 characters, about the colour or light of the photo "
        "(for example \"Red earth evening\" or \"Blue hour, Udupi\" only if the place is in place_name). Leave the other overlay fields empty."
    ),
    "film": (
        "Today's photo is shown on a strip of film. overlay_title is a short title under it, 1 to 4 words, at most 32 characters, "
        "or two short phrases separated by a comma (for example \"Late bus, warm light\"). Leave the other overlay fields empty."
    ),
    "popout": (
        "Today's photo is an editorial poster: the photo sits on paper and its main subject steps out of the frame. "
        "Write the words for it; code sets them in capitals, spaces them and places them:\n"
        "- overlay_kicker: a small label above the title, 1 to 3 words, at most 24 characters (for example \"Road trips\" or \"Monsoon diaries\").\n"
        "- overlay_title: the big title, 1 to 3 words, at most 20 characters, that makes sense on its own (for example "
        "\"Somewhere ahead\" or \"Into the wild\").\n"
        "- overlay_line: one spaced line under the title, 3 to 6 words, at most 40 characters, about the feeling of the "
        "moment rather than the objects (for example \"Good roads, brighter days\").\n"
        "- overlay_tags: 0 to 4 single words for the foot of the page, at most 12 characters each (for example "
        "Roads, Photos, People, Stories).\n"
        "Leave overlay_note and overlay_marks empty."
    ),
    "story": (
        "Today's post is a short story in slides: the cover with a title over it, the other photos, then a closing slide. "
        "overlay_title is the title on the cover, 2 to 5 words, at most 32 characters. overlay_line is the one sentence "
        "on the closing slide, at most 80 characters, that ends the story. Leave overlay_note empty."
    ),
    "contact_sheet": (
        "Today's post is the best photo of a set, then all of them on a film contact sheet with the best one circled in red "
        "grease pencil. overlay_title is what the photographer scrawls on the sheet in that pencil: 1 to 4 words, at most "
        "24 characters, like a note to self (for example \"the keeper\" or \"this one, the light\"). Code adds the place and month. "
        "Leave the other overlay fields empty."
    ),
    "seamless": (
        "Today's photos are laid across joined slides with one hand-drawn line running through them, like a route. "
        "overlay_title is written by hand at the start of that line: 2 to 5 words, at most 28 characters, naming the journey "
        "or the walk (for example \"From the ghats to the sea\" or \"One slow Sunday\"). Leave the other overlay fields empty."
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

    name: str = "any"
    mood: str | None = None
    category: str | None = None
    any_styles: list[str] = field(default_factory=lambda: list(DESIGNED))
    switched_by: str = "selection.style in the config"

    @property
    def plain(self) -> bool:
        return self.name == "single"

    @property
    def any(self) -> bool:
        return self.name == "any"

    @property
    def sets_need_one_shape(self) -> bool:
        """A plain multi-photo post shows every photo in the cover's shape; a designed style redraws them."""
        return self.plain or (self.any and "single" in self.any_styles)

    def options_for_planner(self) -> dict | None:
        """What the planner is told when it chooses the style ("any"); None when the style is fixed."""
        if not self.any:
            return None
        return {s: STYLE_GUIDE[s] for s in self.any_styles}

    @property
    def wants_set(self) -> bool:
        """Whether the planner should be asked for several photos whatever the multi-photo switch says."""
        return self.name in MULTI_STYLES

    @property
    def single_only(self) -> bool:
        """Whether today's style holds exactly one photo, so the planner shouldn't offer a set."""
        return self.name in SINGLE_STYLES


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
    unknown or retired value is ignored with a warning, never an error, so a typo can't stop the daily post."""
    settings = dict(selection.get("style") or {})
    request = StyleRequest()
    listed = settings.get("any_styles") or settings.get("auto_styles")  # auto_styles: the older name
    if listed:
        request.any_styles = [s for s in listed if s in STYLES and s != "any"] or list(DESIGNED)

    name = str(settings.get("name", "any")).strip().lower()
    if (env := _env("POST_STYLE")) and env not in ("", "variable"):
        name, request.switched_by = env, f"POST_STYLE={env}"
    name = ALIASES.get(name, name)
    if name in RETIRED:
        log.warning("The %s style was retired; letting the planner choose. Choose one of: %s.", name, ", ".join(STYLES))
        name = "any"
    elif name not in STYLES:
        log.warning("Unknown post style %r; letting the planner choose. Choose one of: %s.", name, ", ".join(STYLES))
        name = "any"
    request.name = name

    request.mood = _pick("mood", MOODS, _env("POST_MOOD"), settings.get("mood"))
    request.category = _pick("category", CATEGORIES, _env("POST_CATEGORY"), settings.get("category"))
    return request


def grid_size(count: int) -> int:
    return next((n for n in GRID_SIZES if n <= count), 0)


def fits(style: str, photo_count: int, wide: bool) -> bool:
    if style in MULTI_STYLES:
        return photo_count >= SET_MINIMUM[style]
    if style == "panorama":
        return wide
    return True  # single-photo styles take the cover; any other photos are left for later


def popout_ready(cover: dict, available: bool = True) -> str | None:
    """Why the pop-out can't be used for this photo, from the analyst's notes alone (no model run yet),
    or None when it may be tried. The mask's own checks come later, in imaging/cutout.py."""
    if not available:
        return 'the cut-out model isn\'t installed (pip install -e ".[cutout]")'
    if cover.get("is_devotional"):
        return "nothing is cut out of a photo of a deity, shrine or prayer"
    if cover.get("has_children"):
        return "photos with children are never cut out"
    values = [cover.get(k) for k in ("subject_top", "subject_bottom", "subject_left", "subject_right")]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in values):
        return "the analyst gave no subject box"
    top, bottom, left, right = values
    area = max(0.0, bottom - top) * max(0.0, right - left)
    if not 0.06 <= area <= 0.70:
        return "the subject is too small or fills the whole photo"
    return None


def details_ready(cover: dict) -> str | None:
    """Why a detail walk can't be drawn from the analyst's notes, or None. Crops follow the anchors,
    so it needs two things to look at that aren't people."""
    if len(usable_anchors(cover)) < 2:
        return "the analyst found fewer than two things to look at closely"
    return None


def choose_style(
    request: StyleRequest,
    *,
    photo_count: int,
    cover: dict,
    today: date,
    last_style: str | None = None,
    recent_styles: tuple[str, ...] | list[str] = (),
    suggested: str | None = None,
    popout_available: bool = False,
    sequence_ok: bool = True,
) -> tuple[str, str | None]:
    """The style today's post is drawn in, given the photos the planner chose, and a note when the
    requested style couldn't be used. With "any", the planner's suggestion wins when it is allowed,
    fits the photos and isn't the last post's style; otherwise code picks one that suits them and
    wasn't used in the last two posts. A fixed style falls back step by step: a set style with one
    photo, a panorama of a photo that isn't wide, or a style the photo can't take becomes a palette."""
    shape = cover.get("shape") or 1.0
    wide = shape >= PANORAMA_MIN_SHAPE
    name = request.name
    devotional = bool(cover.get("is_devotional"))
    recent = [s for s in ([last_style] if last_style else []) + list(recent_styles) if s][:2]
    if name == "any":
        allowed = [s for s in request.any_styles if not (s == "notes" and devotional)]  # never draw on a devotional photo
        if "popout" in allowed and popout_ready(cover, popout_available):
            allowed.remove("popout")
        if "details" in allowed and details_ready(cover):
            allowed.remove("details")
        if "sequence" in allowed and not sequence_ok:  # the photos weren't taken minutes apart
            allowed.remove("sequence")
        if suggested in allowed and fits(suggested, photo_count, wide) and suggested not in recent[:1]:
            return suggested, None
        if photo_count > 1:
            options = [s for s in MULTI_STYLES if s in allowed and photo_count >= SET_MINIMUM[s]]
            options = options or (["single"] if "single" in allowed else [])
            if not options:  # no set style allowed: the cover alone, in a single-photo style
                return choose_style(request, photo_count=1, cover=cover, today=today, recent_styles=recent, popout_available=popout_available)
        else:
            suits = MOOD_STYLES.get(cover.get("mood") or "", ("palette",))
            notes_fit = ("notes",) if cover.get("category") in NOTES_CATEGORIES else ()
            options = [s for s in (*suits, *notes_fit, "details", "film", "palette", "single") if s in allowed and (s != "panorama" or wide)]
            options = list(dict.fromkeys(options))
        options = [s for s in options if s not in recent] or options  # a different style from the last two posts when possible
        return (random.Random(today.isoformat()).choice(options) if options else FALLBACK), None
    if name in MULTI_STYLES and photo_count < SET_MINIMUM[name]:
        return FALLBACK, f"The {name.replace('_', ' ')} style needs several photos from one outing or place, so today's photo was set as a colour palette instead."
    if name == "popout" and (why := popout_ready(cover, popout_available)):
        return FALLBACK, f"The pop-out needs one clear subject to cut out, but {why}, so today's photo was set as a colour palette instead."
    if name == "details" and (why := details_ready(cover)):
        return FALLBACK, f"A detail walk needs things to crop in on, but {why}, so today's photo was set as a colour palette instead."
    if name == "notes" and devotional:
        return FALLBACK, "Nothing is drawn on a photo of a deity, shrine or prayer, so it was set as a colour palette instead."
    if name == "panorama" and not wide:
        return FALLBACK, "The photo isn't wide enough to split into a panorama, so it was set as a colour palette instead."
    return name, None


# --- looks: backgrounds, frames and drawn marks, chosen by rules ---------------------------

POPOUT_BACKDROPS = ("paper", "handmade", "linen", "kraft")
BACKDROP_NAMES = ("paper", "palette", "echo", "swatches", "two_tone", "sun", "kraft", "linen", "handmade", "slate", "navy", "newsprint")
FRAME_NAMES = ("polaroids",)
MARK_NAMES = (
    "field_notes", "doodles", "constellation", "date_stamp", "viewfinder", "highlight", "thirds", "tape", "cover",
    "orbit", "rays", "birds", "aside",
)

# Backgrounds that suit each mood, and the ones a category leans to. Code picks among them by the date.
MOOD_BACKDROPS = {
    "calm": ("palette", "linen", "swatches", "echo"),
    "nostalgic": ("kraft", "paper", "handmade", "newsprint"),
    "romantic": ("handmade", "palette", "sun"),
    "awe": ("echo", "navy", "slate", "sun"),
    "dramatic": ("slate", "echo", "navy"),
    "joyful": ("two_tone", "sun", "palette", "swatches"),
    "energetic": ("two_tone", "echo", "swatches"),
    "playful": ("two_tone", "swatches", "sun"),
}
CATEGORY_BACKDROPS = {"night": ("navy", "echo"), "food": ("swatches", "palette"), "street": ("newsprint", "slate"), "sky": ("sun", "echo")}
# The marks a notes post can carry, by category; each option is one combination.
CATEGORY_MARKS = {
    "food": (("field_notes", "doodles"), ("highlight",), ("aside",)),
    "street": (("date_stamp", "field_notes"), ("aside", "date_stamp")),
    "travel": (("field_notes", "tape"), ("tape", "date_stamp"), ("birds", "aside")),
    "landscape": (("viewfinder",), ("thirds",), ("field_notes", "date_stamp"), ("birds",)),
    "sky": (("viewfinder",), ("doodles",), ("birds",), ("rays",)),
    "night": (("constellation", "date_stamp"), ("orbit", "date_stamp"), ("rays", "aside"), ("viewfinder",)),
    "city": (("cover",), ("field_notes",)),
    "macro": (("highlight",), ("orbit",), ("thirds",)),
    "abstract": (("highlight",), ("thirds",), ("orbit",)),
    "animals": (("field_notes",), ("orbit", "aside"), ("viewfinder",)),
    "event": (("doodles", "date_stamp"), ("field_notes",)),
    "people": (("date_stamp", "viewfinder"), ("tape",), ("thirds",)),
}
# Marks allowed when a person is in the photo: none of them is drawn on or around a person.
PEOPLE_SAFE_MARKS = {"date_stamp", "tape", "thirds", "viewfinder"}
NEEDS_ANCHORS = {"field_notes", "highlight"}
NEEDS_SUBJECT = {"orbit", "rays"}  # a ring or rays need a lone subject with room around it


@dataclass(frozen=True)
class Look:
    backdrop: str | None = None  # the paper behind a pop-out
    frame: str | None = None  # "polaroids" for a small grid
    marks: tuple[str, ...] = ()  # drawn on a notes post

    @property
    def name(self) -> str:
        return "+".join([*filter(None, (self.backdrop, self.frame)), *self.marks]) or "plain"


def _allowed(settings: dict, key: str, known: tuple[str, ...]) -> tuple[str, ...]:
    listed = settings.get(key)
    if not listed:
        return known
    kept = tuple(s for s in listed if s in known)
    return kept or known


def usable_anchors(analysis: dict) -> list[dict]:
    """The analyst's anchors code may point at: never a person, and only with sensible numbers."""
    out = []
    for a in analysis.get("anchors") or []:
        try:
            x, y = float(a.get("x")), float(a.get("y"))
        except (TypeError, ValueError, AttributeError):
            continue
        if a.get("is_person") or not (0 <= x <= 1 and 0 <= y <= 1) or not str(a.get("name") or "").strip():
            continue
        out.append({"name": str(a["name"]).strip()[:40], "x": x, "y": y})
    return out[:3]


def lone_subject(analysis: dict) -> bool:
    """Whether the analyst's subject band leaves room for a ring or rays around it."""
    try:
        top, bottom = float(analysis["subject_top"]), float(analysis["subject_bottom"])
        left, right = float(analysis.get("subject_left", 0)), float(analysis.get("subject_right", 1))
    except (KeyError, TypeError, ValueError):
        return False
    return 0 < bottom - top <= 0.5 and 0 < right - left <= 0.5


def choose_look(
    style: str,
    analysis: dict,
    *,
    seed: int,
    photo_count: int = 1,
    last_look: str | None = None,
    has_time: bool = False,
    has_place: bool = False,
    settings: dict | None = None,
) -> Look:
    """Today's look for a styled post, by rule: the mood and category choose the candidates, the seed
    picks one, and yesterday's look is avoided when there is another. Never an AI call."""
    settings = settings or {}
    rng = random.Random(seed)
    mood, category = analysis.get("mood") or "", analysis.get("category") or ""
    backdrops = _allowed(settings, "backdrops", BACKDROP_NAMES)
    frames = _allowed(settings, "frames", FRAME_NAMES)
    allowed_marks = set(_allowed(settings, "marks", MARK_NAMES))

    def pick(options: list[Look]) -> Look:
        fresh = [o for o in options if o.name != last_look] or options
        return rng.choice(fresh) if fresh else Look()

    def backdrop_options() -> list[str]:
        liked = (*CATEGORY_BACKDROPS.get(category, ()), *MOOD_BACKDROPS.get(mood, ("palette", "paper")))
        return [b for b in dict.fromkeys(liked) if b in backdrops] or list(backdrops)  # none suits: any the config allows

    if style == "popout":  # the subject stands on the page, so the page must be a light paper
        light = [b for b in backdrop_options() if b in POPOUT_BACKDROPS] or [b for b in POPOUT_BACKDROPS if b in backdrops] or ["paper"]
        return pick([Look(backdrop=b) for b in light])
    if style == "grid":
        if 2 <= photo_count <= 4 and "polaroids" in frames and rng.random() < 0.5:
            return Look(frame="polaroids")
        return Look()
    if style == "notes":
        anchors = usable_anchors(analysis)
        people = bool(analysis.get("has_people") or analysis.get("has_children"))
        combos = CATEGORY_MARKS.get(category, (("viewfinder",), ("thirds",)))

        def ok(combo: tuple[str, ...]) -> bool:
            if any(m not in allowed_marks for m in combo):
                return False
            if people and not set(combo) <= PEOPLE_SAFE_MARKS:
                return False
            if NEEDS_ANCHORS & set(combo) and not anchors:
                return False
            if NEEDS_SUBJECT & set(combo) and not lone_subject(analysis):
                return False
            if "date_stamp" in combo and not has_time:
                return False
            return not ("tape" in combo and not has_place)

        options = [Look(marks=c) for c in combos if ok(c)]
        if not options:  # always something to draw: corner brackets need nothing from the photo
            options = [Look(marks=(m,)) for m in ("viewfinder", "thirds", "date_stamp") if m in allowed_marks and (m != "date_stamp" or has_time)]
        return pick(options or [Look(marks=("viewfinder",))])
    return Look()


DETAILS_BRIEF = (
    "Today's post is the photo, then a close crop on each of the things below, in the same order, each with a short "
    "handwritten label. overlay_marks: one label for each thing, 2 to 5 words and at most 28 characters each, like a "
    "photographer pointing out what they noticed (for example \"still warm\" or \"hand-painted, by the look of it\"). "
    "Describe only what is visible. Leave the other overlay fields empty.\n"
)


def notes_brief(look: Look, anchors: list[dict]) -> str:
    """What the caption writer is told about the words drawn on a notes post."""
    parts = [
        "Today's post is the photo with a few hand-drawn marks on top "
        f"({', '.join(m.replace('_', ' ') for m in look.marks)})."
    ]
    things = "\n".join(f"{n}. {a['name']}" for n, a in enumerate(anchors, 1))
    if {"field_notes", "highlight"} & set(look.marks) and anchors:
        parts.append(
            "overlay_marks: one handwritten note for each thing below, in the same order, 2 to 4 words and at most 28 characters each, "
            "like a diary aside about that thing (for example \"still warm\" or \"the six pm light\"). Describe only what is visible.\n"
            + things
        )
    if "cover" in look.marks:
        parts.append(
            "The photo is set as a magazine cover. overlay_title is the masthead across it: one or two words, at most 16 characters "
            "(for example \"Coastline\"), never a real magazine's name."
            + (" overlay_marks are up to two short cover lines, at most 28 characters each, about these things:\n" + things if anchors else "")
        )
    if "aside" in look.marks:
        parts.append(
            "overlay_note is one handwritten aside written on the photo, 1 to 4 words, at most 24 characters, like the photographer "
            "jotting a thought beside the subject (for example \"hello, moon\" or \"still here\"). About what is visible; no hashtags or emoji."
        )
    if "constellation" in look.marks:
        parts.append("overlay_note is a tiny label for the star map drawn in the sky, 2 to 4 words, at most 32 characters (for example \"the quiet seven\").")
    if "thirds" in look.marks:
        parts.append("overlay_line is one sentence on why the frame works, at most 80 characters (for example \"The horizon sits on the lower third.\").")
    if len(parts) == 1:
        parts.append("Code adds everything drawn today (the date, place or time), so leave every overlay field empty.")
    parts.append("Leave any overlay field not named here empty.")
    return "\n\n".join(parts)


def look_brief(style: str, look: Look | None, anchors: list[dict] | None = None) -> str:
    """The caption writer's brief for a styled post, with the look's own words when it has marks."""
    if style == "details":
        things = "\n".join(f"{n}. {a['name']}" for n, a in enumerate(anchors or [], 1))
        return f"{DETAILS_BRIEF}{things}\n\n{TEXT_RULES}"
    if style not in BRIEFS and style != "notes":
        return ""
    if look is not None and style == "notes":
        return f"{notes_brief(look, anchors or [])}\n\n{TEXT_RULES}"
    return brief_for(style)
