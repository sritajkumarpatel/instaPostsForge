"""The pop-out poster: the photo shown through a window on paper, with its main subject stepping out of
the window onto the page, like an editorial print.

Nothing here edits the photo. The photo is scaled once, then pasted through a mask made of three parts:
the window (a plain rectangle), the subject (from imaging/cutout.py) and, when the subject steps down,
a soft patch of the photo's own ground under it, so its feet and shadow land on something. Every pixel
that shows at full strength is the scaled photo's own pixel; a test checks this. The only things drawn
are on the paper: a soft shadow under the subject, the type and a thin rule.

The variant (how the subject leaves the window) comes from where the subject sits and where the photo
has room (cutout.fitting_variants), chosen by the seed. The words come from the caption writer
(kicker, title, line, up to four tags); everything about how they look is decided here, by rule.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import palette as pal
from .backdrops import Backdrop, backdrop as make_backdrop
from .layouts import font, line_height, text_width

W, H = 1080, 1350
MARGIN = 64
LIGHT_BACKDROPS = ("paper", "handmade", "linen", "kraft")  # the look depends on a light paper


@dataclass
class PopoutText:
    kicker: str | None = None
    title: str | None = None
    line: str | None = None
    tags: tuple[str, ...] = ()


@dataclass
class Placed:
    """Where everything landed, so tests (and the run summary) can check the layout."""

    image: Image.Image
    variant: str
    photo_box: tuple[int, int, int, int]  # the scaled, cropped photo on the page
    window: tuple[int, int, int, int]  # the window on the page
    mask: Image.Image  # the full mask the photo was pasted through, page-sized
    subject: Image.Image  # the subject's mask on the page, page-sized
    shown: Image.Image  # the scaled photo, page-sized and aligned, for checking pixels
    text_boxes: list[tuple[int, int, int, int]]


# --- type -----------------------------------------------------------------------------


def _spaced(text: str) -> str:
    return " ".join(text.upper().split())


def _small_line(draw, text: str, size: int, width: int, spacing_em: float = 0.45):
    """A line of small, widely spaced capitals that fits the width: the font shrinks before it wraps."""
    text = _spaced(text)
    while size > 14:
        face = font("small", size, "Medium")
        spacing = round(size * spacing_em)
        if text_width(draw, text, face, spacing) <= width:
            return face, spacing, text
        size -= 1
    face = font("small", 14, "Medium")
    return face, 4, text


def _draw_spaced_centre(draw, y: int, text: str, face, spacing: int, fill, cx: int = W // 2) -> tuple[int, int, int, int]:
    w = text_width(draw, text, face, spacing)
    x = cx - w // 2
    for ch in text:
        draw.text((x, y), ch, font=face, fill=fill)
        x += text_width(draw, ch, face) + spacing
    return (cx - w // 2, y, cx - w // 2 + w, y + line_height(face))


def _title_face(draw, text: str, width: int, start: int = 132):
    """The title's capitals at the largest size that fits: one line down to 84 px, then two lines."""
    words = _spaced(text).split()
    for size in range(start, 83, -4):
        face, spacing = font("serif", size), round(size * 0.03)
        if text_width(draw, " ".join(words), face, spacing) <= width:
            return face, spacing, [" ".join(words)]
    for size in range(start, 40, -4):
        face, spacing = font("serif", size), round(size * 0.03)
        for cut in range(1, len(words)):
            lines = [" ".join(words[:cut]), " ".join(words[cut:])]
            if all(text_width(draw, ln, face, spacing) <= width for ln in lines):
                return face, spacing, lines
    return font("serif", 40), 1, [" ".join(words)]


def title_ink(colours: list[pal.RGB], paper: pal.RGB) -> pal.RGB:
    """The darkest photo colour that reads on the paper (4.5:1), like a green WILD over a forest photo;
    otherwise the usual near-black."""
    for colour in sorted(colours, key=pal.luminance):
        deep = pal.deep_tone(colour)
        for candidate in (colour, deep):
            if pal.contrast(candidate, paper) >= 4.5 and not _grey(candidate):
                return candidate
    return pal.ink_for(paper)


def _grey(c: pal.RGB) -> bool:
    return max(c) - min(c) < 18


def text_block(canvas: Image.Image, text: PopoutText, top: int, ink: pal.RGB, soft: pal.RGB, title_colour: pal.RGB, measure_only: bool = False) -> tuple[int, list]:
    """Kicker, title, line and a short rule, centred from `top`. Returns the bottom edge and the boxes."""
    draw = ImageDraw.Draw(canvas)
    width = W - 2 * MARGIN
    y, boxes = top, []

    def put(box):
        boxes.append(box)
        return box[3]

    if text.kicker:
        face, spacing, s = _small_line(draw, text.kicker, 24, width)
        y = put(_draw_spaced_centre(draw, y, s, face, spacing, soft) if not measure_only else (MARGIN, y, W - MARGIN, y + line_height(face))) + 16
    if text.title:
        face, spacing, lines = _title_face(draw, text.title, width)
        for ln in lines:
            box = _draw_spaced_centre(draw, y, ln, face, spacing, title_colour) if not measure_only else (MARGIN, y, W - MARGIN, y + line_height(face))
            y = put(box) - round(face.size * 0.12)
        y += 18
    if text.line:
        face, spacing, s = _small_line(draw, text.line, 22, width)
        y = put(_draw_spaced_centre(draw, y, s, face, spacing, soft) if not measure_only else (MARGIN, y, W - MARGIN, y + line_height(face))) + 22
    if boxes:
        if not measure_only:
            draw.line((W // 2 - 45, y, W // 2 + 45, y), fill=soft, width=2)
        boxes.append((W // 2 - 45, y - 1, W // 2 + 45, y + 1))
        y += 2
    return y, boxes


def tags_row(canvas: Image.Image, tags: tuple[str, ...], bottom: int, soft: pal.RGB, measure_only: bool = False) -> tuple[int, list]:
    """Up to four single words along the foot of the page, with dots between. Returns the top edge."""
    if not tags:
        return bottom, []
    draw = ImageDraw.Draw(canvas)
    text = "  ·  ".join(_spaced(t) for t in tags[:4])
    face, spacing, text = _small_line(draw, text, 17, W - 2 * MARGIN, 0.4)
    top = bottom - line_height(face)
    box = _draw_spaced_centre(draw, top, text, face, spacing, soft) if not measure_only else (MARGIN, top, W - MARGIN, bottom)
    return top, [box]


# --- the window and the masks -----------------------------------------------------------


def _bbox(mask: Image.Image) -> tuple[int, int, int, int]:
    box = mask.point(lambda v: 255 if v >= 128 else 0).getbbox()
    if box is None:
        raise ValueError("empty subject mask")
    return box


def window_for(variant: str, size: tuple[int, int], subject: tuple[int, int, int, int], rng: random.Random) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """The window and the part of the photo to show, both in photo pixels, for one variant.

    - step_down: the window holds the subject's upper part; its lower part steps out below, onto a
      patch of its own ground.
    - step_left / step_right: the window stops part-way across the subject, which steps out of that side.
    - break_frame: a window across the subject's middle; the subject crosses its top and bottom.
    """
    w, h = size
    left, top, right, bottom = subject
    sw, sh = right - left, bottom - top
    if variant == "step_down":
        cut = top + round(sh * rng.uniform(0.42, 0.55))
        wt = max(0, top - max(round(sh * 0.3), round(h * 0.06)))
        wl, wr = 0, w
        ground = min(h - bottom, max(round(sh * 0.16), round(h * 0.04)))
        return (wl, wt, wr, cut), (wl, wt, wr, min(h, bottom + ground))
    if variant in ("step_left", "step_right"):
        wt = max(0, top - max(round(sh * 0.15), round(h * 0.05)))
        wb = min(h, bottom + max(round(sh * 0.08), round(h * 0.03)))
        if variant == "step_right":
            cut = left + round(sw * rng.uniform(0.5, 0.62))
            wl = max(0, cut - max(round(w * 0.55), round(sw * 1.2)))
            room = min(w, right + round(w * 0.02))
            return (wl, wt, cut, wb), (wl, wt, room, wb)
        cut = right - round(sw * rng.uniform(0.5, 0.62))
        wr = min(w, cut + max(round(w * 0.55), round(sw * 1.2)))
        room = max(0, left - round(w * 0.02))
        return (cut, wt, wr, wb), (room, wt, wr, wb)
    # break_frame
    wt = top + round(sh * rng.uniform(0.16, 0.24))
    wb = bottom - round(sh * rng.uniform(0.2, 0.3))
    wl = max(0, left - round(sw * 0.55))
    wr = min(w, right + round(sw * 0.55))
    pad = round(h * 0.01)
    return (wl, wt, wr, wb), (wl, max(0, top - pad), wr, min(h, bottom + pad))


def ground_mask(size: tuple[int, int], subject: Image.Image, box: tuple[int, int, int, int], seed: int) -> Image.Image:
    """A soft, ragged patch under the subject where its own ground shows, fading to nothing before any
    edge of the shown photo, so no straight photo edge appears on the page."""
    w, h = size
    left, top, right, bottom = box
    cx, sw, sh = (left + right) / 2, right - left, bottom - top
    gw, gh = sw * 0.62, max(sh * 0.13, 12)
    patch = Image.new("L", size, 0)
    ImageDraw.Draw(patch).ellipse((cx - gw, bottom - gh * 1.6, cx + gw, bottom + gh), fill=255)
    patch = patch.filter(ImageFilter.GaussianBlur(max(8, round(gh * 0.9))))
    rng = random.Random(seed)
    noise = Image.effect_noise((max(1, w // 10), max(1, h // 10)), 70 + rng.randint(0, 20)).resize(size, Image.BICUBIC)
    patch = ImageChops.multiply(patch, noise.point(lambda v: min(255, v + 120)))
    fade = max(10, round(min(w, h) * 0.06))
    edge = Image.new("L", size, 0)
    ImageDraw.Draw(edge).rectangle((fade, 0, w - fade, h - fade), fill=255)
    return ImageChops.multiply(patch, edge.filter(ImageFilter.GaussianBlur(fade // 2)))


# --- the page ------------------------------------------------------------------------------


def popout(
    photo: Image.Image,
    subject_mask: Image.Image,
    variants: list[str],
    text: PopoutText,
    *,
    seed: int = 0,
    backdrop: Backdrop | None = None,
    variant: str | None = None,
) -> Placed | None:
    """The pop-out page, or None when the words and the photo can't share it without touching (the
    caller then falls back to a plain poster)."""
    if not variants:
        return None
    rng = random.Random(seed)
    variant = variant if variant in variants else rng.choice(variants)
    back = backdrop if backdrop and backdrop.kind in LIGHT_BACKDROPS else make_backdrop(rng.choice(LIGHT_BACKDROPS), photo, seed)
    canvas = back.image.copy()
    paper = canvas.resize((1, 1), Image.BOX).getpixel((0, 0))
    colours = back.colours or pal.palette(photo)
    ink, soft = back.ink, back.soft
    title_colour = title_ink(colours, paper)

    # Words first: they decide how much of the page the photo may take.
    text_bottom, _ = text_block(canvas.copy(), text, MARGIN + 10, ink, soft, title_colour, measure_only=True)
    foot_top, _ = tags_row(canvas.copy(), text.tags, H - MARGIN, soft, measure_only=True)
    gap = 40
    area = (MARGIN, text_bottom + gap if text_bottom > MARGIN + 10 else MARGIN, W - MARGIN, foot_top - gap)
    aw, ah = area[2] - area[0], area[3] - area[1]
    if ah < 0.38 * H:
        return None

    subject = _bbox(subject_mask)
    window, shown = window_for(variant, photo.size, subject, rng)
    sx0, sy0, sx1, sy1 = shown
    scale = min(aw / (sx1 - sx0), ah / (sy1 - sy0), 1.0)
    if (sx1 - sx0) * scale < 0.55 * aw and (sy1 - sy0) * scale < 0.55 * ah:
        return None  # too small to read as a poster
    crop_w, crop_h = round((sx1 - sx0) * scale), round((sy1 - sy0) * scale)
    shown_img = photo.crop(shown).resize((crop_w, crop_h), Image.LANCZOS)
    subj = subject_mask.crop(shown).resize((crop_w, crop_h), Image.LANCZOS)
    px = area[0] + (aw - crop_w) // 2
    py = area[1] + (ah - crop_h) // 2

    def page(box):
        return (px + round((box[0] - sx0) * scale), py + round((box[1] - sy0) * scale), px + round((box[2] - sx0) * scale), py + round((box[3] - sy0) * scale))

    win = page(window)
    local_win = (win[0] - px, win[1] - py, win[2] - px, win[3] - py)
    mask = Image.new("L", (crop_w, crop_h), 0)
    ImageDraw.Draw(mask).rectangle((local_win[0], local_win[1], local_win[2] - 1, local_win[3] - 1), fill=255)
    mask = ImageChops.lighter(mask, subj)
    if variant == "step_down":
        sb = page(subject)
        local_subject = (sb[0] - px, sb[1] - py, sb[2] - px, sb[3] - py)
        mask = ImageChops.lighter(mask, ground_mask((crop_w, crop_h), subj, local_subject, seed))

    # A soft shadow on the paper under the subject, never on the photo.
    depth = max(6, round(min(crop_w, crop_h) * 0.012))
    shadow = subj.filter(ImageFilter.GaussianBlur(depth * 2)).point(lambda v: round(v * 0.3))
    canvas.paste(pal.mix(paper, (0, 0, 0), 0.55), (px + depth, py + round(depth * 1.4)), shadow)
    canvas.paste(shown_img, (px, py), mask)

    _, top_boxes = text_block(canvas, text, MARGIN + 10, ink, soft, title_colour)
    _, foot_boxes = tags_row(canvas, text.tags, H - MARGIN, soft)

    full_mask = Image.new("L", (W, H), 0)
    full_mask.paste(mask, (px, py))
    full_subject = Image.new("L", (W, H), 0)
    full_subject.paste(subj, (px, py))
    full_shown = Image.new("RGB", (W, H), (0, 0, 0))
    full_shown.paste(shown_img, (px, py))
    return Placed(canvas, variant, (px, py, px + crop_w, py + crop_h), win, full_mask, full_subject, full_shown, top_boxes + foot_boxes)


def alt_suffix(variant: str) -> str:
    """What the alt text adds, always: the layout is a composite, and the photo itself isn't edited."""
    return " Layout: the subject is cut out of the original photo and layered over paper; the photo itself is not edited."
