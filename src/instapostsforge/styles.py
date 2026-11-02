"""Post styles: how a day's photo or photos are laid out before they go to Instagram.

"any" is the default: every post is drawn in one of the designed styles below, and the AI planner
chooses which one suits each day's photos. "single" is the plain photo as shot, used only when chosen.
The designed styles are drawn by code (imaging/layouts.py) from templates:

- grid: 2 to 9 photos of one outing or place tiled on one slide, then each photo on its own slide.
- gallery: one photo on a wide margin with a small museum-style label (title, place, month).
- poster: one photo as a print on warm paper with a spaced title, a quiet line and a handwritten note.
- panorama: one wide photo cut into 2 or 3 slides that join up as you swipe.
- story: a title slide over the cover, the other photos on gallery margins, and a closing line.
- popout: an editorial poster on paper: the photo shows through a window, and its main subject steps
  out of the window onto the page (cut out by a local model in imaging/cutout.py, never repainted),
  under a large serif title and widely spaced small lines.
- notes: one image, the photo with a hand-drawn layer on top (notes with arrows, a pen ring or rays,
  doodles, a star map, a film date stamp, viewfinder marks and more). The photo's pixels are unchanged.
- any: the planner picks one of selection.style.any_styles for each post from the photos' notes; if
  its pick can't be drawn, code picks one that suits the photos' mood. "auto" means the same.

The AI's part is choosing the photos (the planner, as for multi-photo posts) and writing the few
words that go on the image (the caption writer's overlay_* fields). Code checks those words like the
caption, and a style that can't be drawn falls back to a simpler one, never to a broken post.

On top of the style, code picks a "look" by plain rules (choose_look): the background behind a poster
or gallery print, a frame (postcard, ticket, film, magazine page, festive border, polaroids), or the
marks drawn for a notes post. The look follows the photo's mood and category, never repeats yesterday's,
and changes with the date. No AI call is spent on it.

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

STYLES = ("any", "single", "grid", "gallery", "poster", "panorama", "story", "notes", "popout")
DESIGNED = ("poster", "gallery", "grid", "story", "panorama", "notes", "popout")  # what "any" chooses from by default
ALIASES = {"auto": "any"}
# Styles that put several photos in one post, and the most photos each may hold. A carousel has at
# most 10 slides: a grid adds its collage slide, and a story adds a closing slide.
MULTI_STYLES = {"grid": 9, "story": 9}
GRID_SIZES = (9, 6, 4, 3, 2)  # how many photos a grid can hold; others are trimmed to the next size down
PANORAMA_MIN_SHAPE = 1.5  # width:height a photo needs to make at least two joined slides
OFF = {"", "any", "all", "auto", "none", "variable"}  # "no filter" for mood and category

# Short descriptions the planner chooses from when the style is "any".
STYLE_GUIDE = {
    "poster": "one photo as a large print on paper with a short title, a line and a handwritten note; suits calm, nostalgic, romantic or story-telling single photos",
    "gallery": "one photo on a clean margin with a museum-style title label; suits strong, clean single photos of any mood",
    "panorama": "one wide photo (3:2 or wider, shape 1.5 or more) split into joined slides; suits wide landscapes, skylines and seascapes",
    "grid": "3 to 9 photos from one set tiled on one slide, then each on its own; suits outings with many varied photos",
    "story": "2 to 9 photos from one set as a sequence with a title and a closing line; suits outings that unfold over time",
    "notes": "the photo with a few hand-drawn notes, arrows or doodles on top, as one image; suits photos with two or three distinct things to point at, food, street and travel moments, and night skies; never for photos of a deity, shrine or prayer",
    "popout": "an editorial poster: the main subject steps out of the photo's frame onto paper under a big title; only for one clear, sharp subject (a person, vehicle, animal or object) with space around it, never a deity, shrine or prayer, a crowd, a flat scene or a pure landscape",
    "single": "the photo as shot, with no layout",
}

# For any, when the planner's pick can't be used: the styles that suit each mood, best first.
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

NOTES_CATEGORIES = ("food", "street", "travel", "night", "macro")  # where code's own fallback may pick notes

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
    decor_dir: str | None = None
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
        return self.name in ("gallery", "poster", "panorama", "notes", "popout")


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
    listed = settings.get("any_styles") or settings.get("auto_styles")  # auto_styles: the older name
    if listed:
        request.any_styles = [s for s in listed if s in STYLES and s != "any"] or list(DESIGNED)

    name = str(settings.get("name", "any")).strip().lower()
    if (env := _env("POST_STYLE")) and env not in ("", "variable"):
        name, request.switched_by = env, f"POST_STYLE={env}"
    name = ALIASES.get(name, name)
    if name not in STYLES:
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
        return photo_count >= (3 if style == "grid" else 2)
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


def choose_style(
    request: StyleRequest,
    *,
    photo_count: int,
    cover: dict,
    today: date,
    last_style: str | None = None,
    suggested: str | None = None,
    popout_available: bool = False,
) -> tuple[str, str | None]:
    """The style today's post is drawn in, given the photos the planner chose, and a note when the
    requested style couldn't be used. With "any", the planner's suggestion wins when it is allowed and
    fits the photos; otherwise code picks one that suits them. A fixed style falls back step by step:
    a set style with one photo becomes a single-photo style, and a panorama of a photo that isn't wide
    becomes a gallery frame."""
    shape = cover.get("shape") or 1.0
    wide = shape >= PANORAMA_MIN_SHAPE
    name = request.name
    devotional = bool(cover.get("is_devotional"))
    if name == "any":
        allowed = [s for s in request.any_styles if not (s == "notes" and devotional)]  # never draw on a devotional photo
        if "popout" in allowed and popout_ready(cover, popout_available):
            allowed.remove("popout")
        if suggested in allowed and fits(suggested, photo_count, wide):
            return suggested, None
        if photo_count > 1:
            options = [s for s in ("grid", "story") if s in allowed and (s != "grid" or photo_count >= 3)]
            options = options or (["single"] if "single" in allowed else [])
            if not options:  # no set style allowed: the cover alone, in a single-photo style
                return choose_style(request, photo_count=1, cover=cover, today=today, last_style=last_style)
        else:
            suits = MOOD_STYLES.get(cover.get("mood") or "", ("gallery",))
            notes_fit = ("notes",) if cover.get("category") in NOTES_CATEGORIES else ()
            options = [s for s in (*suits, *notes_fit, "poster", "gallery", "single") if s in allowed and (s != "panorama" or wide)]
            options = list(dict.fromkeys(options))
        options = [s for s in options if s != last_style] or options  # a different look from yesterday's when possible
        return (random.Random(today.isoformat()).choice(options) if options else "gallery"), None
    if name in MULTI_STYLES and photo_count < 2:
        fallback = "gallery" if name == "grid" else "poster"
        return fallback, f"The {name} style needs several photos from one outing or place, so today's photo was set as a {fallback} instead."
    if name == "popout" and (why := popout_ready(cover, popout_available)):
        return "poster", f"The pop-out needs one clear subject to cut out, but {why}, so today's photo was set as a poster instead."
    if name == "notes" and devotional:
        return "gallery", "Nothing is drawn on a photo of a deity, shrine or prayer, so it was set in a gallery frame instead."
    if name == "panorama" and not wide:
        return "gallery", "The photo isn't wide enough to split into a panorama, so it was set in a gallery frame instead."
    return name, None


# --- looks: backgrounds, frames and drawn marks, chosen by rules ---------------------------

POPOUT_BACKDROPS = ("paper", "handmade", "linen", "kraft")
BACKDROP_NAMES = ("paper", "palette", "echo", "swatches", "two_tone", "sun", "kraft", "linen", "handmade", "slate", "navy", "newsprint")
FRAME_NAMES = ("postcard", "ticket", "film", "magazine", "festive", "polaroids")
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
# Frames a gallery slide may take instead of a plain margin, by category.
CATEGORY_FRAMES = {
    "travel": ("postcard", "ticket"),
    "landscape": ("postcard", "ticket"),
    "street": ("film",),
    "night": ("film",),
    "city": ("magazine", "film"),
    "abstract": ("magazine",),
    "event": ("magazine",),
    "people": ("film", "magazine"),
}
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
    backdrop: str | None = None  # behind a poster or gallery print
    frame: str | None = None  # a gallery slide's frame, or "polaroids" for a small set
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
    devotional = bool(analysis.get("is_devotional"))
    backdrops = _allowed(settings, "backdrops", BACKDROP_NAMES)
    frames = _allowed(settings, "frames", FRAME_NAMES)
    allowed_marks = set(_allowed(settings, "marks", MARK_NAMES))

    def pick(options: list[Look]) -> Look:
        fresh = [o for o in options if o.name != last_look] or options
        return rng.choice(fresh) if fresh else Look()

    def backdrop_options() -> list[str]:
        liked = (*CATEGORY_BACKDROPS.get(category, ()), *MOOD_BACKDROPS.get(mood, ("palette", "paper")))
        return [b for b in dict.fromkeys(liked) if b in backdrops] or list(backdrops)  # none suits: any the config allows

    if style in ("poster", "story"):
        return pick([Look(backdrop=b) for b in backdrop_options()])
    if style == "popout":  # the subject stands on the page, so the page must be a light paper
        light = [b for b in backdrop_options() if b in POPOUT_BACKDROPS] or [b for b in POPOUT_BACKDROPS if b in backdrops] or ["paper"]
        return pick([Look(backdrop=b) for b in light])
    if style == "gallery":
        if devotional:
            return Look(backdrop="palette", frame="festive") if "festive" in frames else Look(backdrop="palette")
        options = [Look(backdrop=b) for b in backdrop_options()]
        options += [Look(frame=f) for f in CATEGORY_FRAMES.get(category, ()) if f in frames and f != "festive"]
        return pick(options)
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


FRAME_BRIEFS = {
    "postcard": (
        "Today's photo is printed on a postcard. overlay_line is the handwritten message on its back: one warm sentence, "
        "at most 80 characters, as if sent to a friend (for example \"Wish you could hear these waves.\"). Code adds the "
        "place and month on the postmark. Leave overlay_title, overlay_note and overlay_marks empty."
    ),
    "ticket": (
        "Today's photo is set on a ticket stub. overlay_title is the title on the stub: two short phrases separated by a comma, "
        "at most 32 characters (for example \"Hills, slow roads\"). Code adds the place and month. Leave the other overlay fields empty."
    ),
    "film": (
        "Today's photo is shown on a strip of film. overlay_title is a short title under it, 1 to 4 words, at most 32 characters. "
        "Leave the other overlay fields empty."
    ),
    "magazine": (
        "Today's photo is laid out as a magazine page. overlay_title is the headline, 2 to 6 words, at most 32 characters. "
        "overlay_line is the standfirst under it: one sentence, at most 80 characters. Leave overlay_note and overlay_marks empty."
    ),
    "festive": (
        "Today's photo sits in a soft festive border. overlay_title is the title on its label, 1 to 4 words, at most 32 characters, "
        "kept reverent. Code adds the month. Leave the other overlay fields empty."
    ),
    "polaroids": (
        "Today's photos are laid out as instant prints on paper. overlay_title is one handwritten line under them, 2 to 5 words, "
        "at most 32 characters, naming what ties them together. Leave the other overlay fields empty."
    ),
}


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
    """The caption writer's brief for a styled post, with the look's own words when it has a frame or marks."""
    if style not in BRIEFS and style != "notes":
        return ""
    if look is not None and style == "notes":
        return f"{notes_brief(look, anchors or [])}\n\n{TEXT_RULES}"
    if look is not None and look.frame in FRAME_BRIEFS:
        return f"{FRAME_BRIEFS[look.frame]}\n\n{TEXT_RULES}"
    return brief_for(style)
