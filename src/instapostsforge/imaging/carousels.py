"""Carousel styles that open on the photo itself and use the swipe to show something new.

Each one is drawn by code from the photographer's own pixels, which are only cropped, scaled and
placed, never repainted:

- palette: the photo, then a card with the five colours it is made of, named by code.
- details: the photo, then two or three close crops on the things the analyst pointed at, each
  with a small map of where it sits in the frame.
- contact_sheet: the chosen photo, then the whole set on a film contact sheet with the chosen frame
  circled in red grease pencil, then the others.
- seamless: a set laid across one wide canvas cut into slides, so the photos cross the slide edges
  and one hand-drawn line runs through them all.
- sequence: the same scene minutes apart, each slide with the time it was taken (from the photo's
  own clock, never the model) and a row of dots showing where it sits in the run.

Inspired by: @colorpalette.cinema (palette), the zoom-in carousel photographers use (details),
Magnum Photos' contact sheets (contact_sheet), split panoramas and seamless carousels (seamless) and
the passage-of-time carousel (sequence). See docs/STYLES.md.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter

from .layouts import H, W, WHITE, LayoutPhoto, contain, cover, fitted, font, line_height, text_width
from .palette import RGB, contrast, deep_tone, distance, ink_for, luminance, palette, safe_swatches, soft_tint

FULL_BLEED = (0.74, 0.86)  # width:height close enough to 4:5 to fill the slide with a thin trim
GREASE_RED = (206, 38, 38)
SHEET_BLACK = (16, 15, 14)
SHEET_ORANGE = (232, 120, 40)


def opening(photo: LayoutPhoto) -> Image.Image:
    """The photo as the first slide: full-bleed when it is close to 4:5, otherwise whole on a mat in
    a deep tone of its own colours, so nothing is cropped away."""
    ratio = photo.image.width / photo.image.height
    if FULL_BLEED[0] <= ratio <= FULL_BLEED[1]:
        return cover(photo, (W, H))
    colours = palette(photo.image, 3) or [(30, 30, 30)]
    mat = deep_tone(min(colours, key=luminance))
    canvas = Image.new("RGB", (W, H), mat)
    margin = 40
    print_ = contain(photo.image, (W - 2 * margin, H - 2 * margin))
    canvas.paste(print_, ((W - print_.width) // 2, (H - print_.height) // 2))
    return canvas


# --- palette -----------------------------------------------------------------------

# Plain names for colours, picked by nearest match. No brand, flag or party names.
COLOUR_NAMES: tuple[tuple[str, RGB], ...] = (
    ("ink", (28, 28, 34)), ("charcoal", (54, 54, 58)), ("slate", (88, 98, 110)), ("pewter", (140, 144, 148)),
    ("fog", (196, 198, 200)), ("chalk", (238, 236, 230)), ("bone", (226, 216, 196)), ("sand", (214, 190, 150)),
    ("wheat", (226, 200, 140)), ("ochre", (196, 140, 56)), ("turmeric", (226, 168, 40)), ("mustard", (200, 170, 60)),
    ("marigold", (240, 150, 30)), ("apricot", (246, 180, 130)), ("peach", (244, 200, 170)), ("terracotta", (190, 94, 62)),
    ("laterite", (166, 74, 50)), ("rust", (150, 70, 40)), ("brick", (140, 56, 46)), ("chilli", (190, 40, 36)),
    ("rose", (214, 120, 130)), ("blush", (232, 186, 184)), ("plum", (98, 50, 80)), ("mulberry", (120, 40, 70)),
    ("aubergine", (70, 44, 72)), ("mauve", (130, 90, 120)), ("heather", (160, 130, 160)), ("dusk purple", (88, 64, 92)),
    ("lavender", (178, 160, 210)), ("dusk violet", (96, 84, 140)), ("indigo", (54, 56, 110)), ("night blue", (24, 34, 64)),
    ("monsoon blue", (70, 100, 130)), ("sky", (140, 186, 226)), ("haze", (186, 208, 222)), ("peacock", (20, 110, 120)),
    ("teal", (40, 120, 120)), ("sea glass", (150, 200, 186)), ("mint", (186, 222, 196)), ("sage", (150, 166, 130)),
    ("moss", (100, 116, 64)), ("leaf", (80, 140, 60)), ("forest", (36, 74, 46)), ("olive", (110, 110, 50)),
    ("tea", (120, 86, 52)), ("cocoa", (86, 60, 44)), ("walnut", (66, 46, 34)), ("coffee", (100, 70, 50)),
    ("lamplight", (250, 214, 140)), ("ember", (226, 100, 40)), ("stone", (170, 160, 146)), ("dune", (196, 166, 120)),
)


def colour_name(colour: RGB) -> str:
    return min(COLOUR_NAMES, key=lambda named: distance(named[1], colour))[0]


def hex_code(colour: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*colour)


def palette_card(photo: LayoutPhoto, title: str | None = None, n: int = 5) -> tuple[Image.Image, list[tuple[str, str]]]:
    """The photo with the colours it is made of under it, each named by code and given its hex code.
    Returns the card and the (name, hex) pairs, for the alt text. Flag-like sets are thinned first."""
    colours = safe_swatches(palette(photo.image, n)) or [(120, 120, 120)]
    colours = sorted(colours, key=luminance)
    ground = soft_tint(colours[len(colours) // 2], 0.93)
    ink = ink_for(ground)
    soft = ink_for(ground, preferred=tuple(int(c * 0.6 + g * 0.4) for c, g in zip(ink, ground)))
    canvas = Image.new("RGB", (W, H), ground)
    draw = ImageDraw.Draw(canvas)
    count = len(colours)
    gap = 22
    chip = min(160, (W - 160 - gap * (count - 1)) // count)
    title_h = 0
    if title:
        typeface, lines = fitted(draw, title, "serif", 52, W - 200, 1, weight="Medium")
        title_h = line_height(typeface) + 40
    print_ = contain(photo.image, (W - 160, 820 - title_h))
    block = title_h + print_.height + 60 + chip + 110
    top = max(50, (H - block) // 2)
    if title:
        draw.text(((W - text_width(draw, lines[0], typeface)) // 2, top), lines[0], font=typeface, fill=ink)
        top += title_h
    px = (W - print_.width) // 2
    canvas.paste(print_, (px, top))
    chip_y = top + print_.height + 60
    x0 = (W - (chip * count + gap * (count - 1))) // 2
    name_face, hex_face = font("serif", 26, "Medium"), font("small", 24)
    named = []
    for i, colour in enumerate(colours):
        x = x0 + i * (chip + gap)
        draw.rectangle((x, chip_y, x + chip, chip_y + chip), fill=colour)
        if contrast(colour, ground) < 1.3:  # a chip close to the card's colour gets a hairline edge
            draw.rectangle((x, chip_y, x + chip, chip_y + chip), outline=soft, width=1)
        name, code = colour_name(colour), hex_code(colour)
        named.append((name, code))
        y = chip_y + chip + 18
        for word in _two_lines(draw, name, name_face, chip + gap - 4):
            draw.text((x + (chip - text_width(draw, word, name_face)) // 2, y), word, font=name_face, fill=ink)
            y += line_height(name_face) - 4
        draw.text((x + (chip - text_width(draw, code, hex_face)) // 2, y + 4), code, font=hex_face, fill=soft)
    return canvas, named


def _two_lines(draw, text: str, typeface, width: int) -> list[str]:
    if text_width(draw, text, typeface) <= width or " " not in text:
        return [text]
    first, rest = text.split(" ", 1)
    return [first, rest]


# --- details -----------------------------------------------------------------------

DETAIL_ZOOM = 2.4  # how much closer a detail slide is than the whole photo
MIN_DETAIL_PIXELS = 420  # a crop narrower than this in the original would look soft at 1080 wide


@dataclass
class Detail:
    box: tuple[int, int, int, int]  # in the original photo's pixels
    label: str | None


def detail_boxes(photo: LayoutPhoto, anchors: list[tuple[float, float, str | None]]) -> list[Detail]:
    """A 4:5 crop around each anchor (x, y in 0 to 1, and its label), skipping any that would be too
    soft or would mostly repeat an earlier crop."""
    image = photo.image
    width = min(image.width, image.height * W / H) / DETAIL_ZOOM
    height = width * H / W
    if width < MIN_DETAIL_PIXELS:
        return []
    out: list[Detail] = []
    for x, y, label in anchors:
        left = min(max(0.0, x * image.width - width / 2), image.width - width)
        top = min(max(0.0, y * image.height - height / 2), image.height - height)
        box = (round(left), round(top), round(left + width), round(top + height))
        if any(_overlap(box, d.box) > 0.45 for d in out):
            continue
        out.append(Detail(box, label))
    return out


def _overlap(a, b) -> float:
    w = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return w * h / area if area else 0.0


def detail_slide(photo: LayoutPhoto, detail: Detail, number: int) -> Image.Image:
    """One close crop filling the slide, with a small map of the whole photo in the corner showing
    where the crop came from, and the writer's short label."""
    image = photo.image
    canvas = image.crop(detail.box).resize((W, H), Image.LANCZOS)
    fade = Image.linear_gradient("L").resize((W, 360)).point(lambda v: int(v * 0.55))
    canvas.paste(Image.new("RGB", (W, 360), (0, 0, 0)), (0, H - 360), fade)
    thumb = contain(image, (230, 230))
    tx, ty = 56, H - 56 - thumb.height
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((tx - 5, ty - 5, tx + thumb.width + 4, ty + thumb.height + 4), fill=WHITE)
    canvas.paste(thumb, (tx, ty))
    sx, sy = thumb.width / image.width, thumb.height / image.height
    l, t, r, b = detail.box
    draw.rectangle((tx + l * sx, ty + t * sy, tx + r * sx, ty + b * sy), outline=WHITE, width=3)
    draw.rectangle((tx + l * sx - 2, ty + t * sy - 2, tx + r * sx + 2, ty + b * sy + 2), outline=(0, 0, 0), width=1)
    number_face = font("small", 30)
    text_x = tx + thumb.width + 44
    draw.text((text_x, ty + 2), f"detail {number:02d}", font=number_face, fill=(230, 226, 218))
    if detail.label:
        typeface, lines = fitted(draw, detail.label, "hand", 64, W - text_x - 56, 2)
        y = ty + 44
        for ln in lines:
            draw.text((text_x, y), ln, font=typeface, fill=WHITE)
            y += line_height(typeface) - 6
    return canvas


# --- contact sheet ---------------------------------------------------------------------


def _grease_ring(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float], rng: random.Random) -> None:
    """A loose hand-drawn loop around box, gone over twice like a grease pencil."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    rx, ry = (box[2] - box[0]) / 2 + 26, (box[3] - box[1]) / 2 + 22
    start = rng.uniform(0, math.tau)
    for stroke in range(2):
        wobble = rng.uniform(0.02, 0.05)
        points = []
        for k in range(0, 76):
            a = start + k / 70 * math.tau  # runs a little past a full turn, as a quick loop does
            r = 1 + wobble * math.sin(3 * a + stroke) + rng.uniform(-0.01, 0.01)
            points.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
        draw.line(points, fill=GREASE_RED, width=7 - stroke * 2, joint="curve")


def contact_sheet(photos: list[LayoutPhoto], chosen: int = 0, title: str | None = None, label: str | None = None, seed: int = 0) -> Image.Image:
    """The set as frames on a black contact sheet, numbered along the edge like a roll of film, with
    the chosen frame circled in red grease pencil and the writer's title scrawled at the foot."""
    rng = random.Random(seed)
    canvas = Image.new("RGB", (W, H), SHEET_BLACK)
    draw = ImageDraw.Draw(canvas)
    n = len(photos)
    cols = 2 if n <= 4 else 3
    rows = math.ceil(n / cols)
    margin_x, top, bottom = 70, 120, 230
    cell_w = (W - 2 * margin_x) // cols
    cell_h = min((H - top - bottom) // rows, int(cell_w * 1.25))
    grid_top = top + ((H - top - bottom) - cell_h * rows) // 2
    numbers = font("serif", 20, "SemiBold")
    roll = 10 + seed % 20
    if label:
        draw.text((margin_x, 52), label.upper(), font=font("small", 26), fill=SHEET_ORANGE)
    boxes = []
    for i, photo in enumerate(photos):
        col, row = i % cols, i // cols
        x0, y0 = margin_x + col * cell_w, grid_top + row * cell_h
        frame = contain(photo.image, (cell_w - 40, cell_h - 56))
        fx = x0 + (cell_w - frame.width) // 2
        fy = y0 + (cell_h - 36 - frame.height) // 2
        draw.rectangle((fx - 6, fy - 6, fx + frame.width + 5, fy + frame.height + 5), fill=(36, 34, 32))
        canvas.paste(frame, (fx, fy))
        draw.text((fx, fy + frame.height + 10), f"{roll + i}", font=numbers, fill=SHEET_ORANGE)
        draw.text((fx + 56, fy + frame.height + 10), f"{roll + i}A", font=numbers, fill=SHEET_ORANGE)
        boxes.append((fx, fy, fx + frame.width, fy + frame.height))
    _grease_ring(draw, boxes[chosen], rng)
    if title:
        typeface, lines = fitted(draw, title, "hand", 76, W - 2 * margin_x, 1)
        x = (W - text_width(draw, lines[0], typeface)) // 2
        draw.text((x, H - 170), lines[0], font=typeface, fill=GREASE_RED)
    return canvas


# --- seamless strip ------------------------------------------------------------------------


def seamless(photos: list[LayoutPhoto], title: str | None = None, seed: int = 0) -> list[Image.Image]:
    """The set laid across one wide canvas (two slides for two photos, three for more) on a deep
    tone of the cover's own colours, so the photos cross the slide edges and one hand-drawn line
    runs through every slide. The canvas is then cut into slides that join up as you swipe."""
    rng = random.Random(seed)
    n = len(photos)
    slides = 2 if n == 2 else 3
    width = W * slides
    colours = palette(photos[0].image, 4) or [(40, 40, 40)]
    ground = deep_tone(min(colours, key=luminance))
    ink = ink_for(ground, preferred=soft_tint(colours[-1], 0.85))
    canvas = Image.new("RGB", (width, H), ground)
    draw = ImageDraw.Draw(canvas)
    box_w = int(width / (n + 1) * 1.1)
    box_h = int(H * 0.6)
    placed = []
    for i, photo in enumerate(photos):
        cx = width * (i + 1) / (n + 1)
        cy = H * (0.44 if i % 2 == 0 else 0.58)
        print_ = contain(photo.image, (box_w, box_h))
        x, y = round(cx - print_.width / 2), round(cy - print_.height / 2)
        shade = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(shade).rectangle((x + 8, y + 14, x + print_.width + 8, y + print_.height + 14), fill=110)
        canvas.paste(Image.new("RGB", canvas.size, (0, 0, 0)), (0, 0), shade.filter(ImageFilter.GaussianBlur(16)))
        canvas.paste(print_, (x, y))
        placed.append((x, y, x + print_.width, y + print_.height))
    # one line drawn by hand from the first slide to the last, passing under the prints
    points = []
    base = H * 0.86
    phase = rng.uniform(0, math.tau)
    for k in range(0, 241):
        x = 60 + (width - 160) * k / 240
        y = base - 46 * math.sin(phase + x / width * math.tau * (slides * 0.75)) + rng.uniform(-1.2, 1.2)
        points.append((x, y))
    draw.line(points, fill=ink, width=4, joint="curve")
    ex, ey = points[-1]
    draw.line([(ex - 24, ey - 16), (ex, ey), (ex - 26, ey + 14)], fill=ink, width=4, joint="curve")
    draw.ellipse((points[0][0] - 8, points[0][1] - 8, points[0][0] + 8, points[0][1] + 8), fill=ink)
    if title:
        typeface, lines = fitted(draw, title, "hand", 84, W - 140, 1)
        draw.text((70, 70), lines[0], font=typeface, fill=ink)
    return [canvas.crop((W * i, 0, W * (i + 1), H)) for i in range(slides)]


# --- light sequence --------------------------------------------------------------------------

MAX_SEQUENCE_MINUTES = 180  # a sequence is one scene changing, not a whole day


def sequence_order(times: list[datetime | None]) -> list[int] | None:
    """The order to show a sequence in: the cover first, then the rest by the time they were taken.
    None when a time is missing or the photos span more than MAX_SEQUENCE_MINUTES."""
    if len(times) < 2 or any(t is None for t in times):
        return None
    span = (max(times) - min(times)).total_seconds() / 60
    if span > MAX_SEQUENCE_MINUTES or len({t for t in times}) < len(times):
        return None
    rest = sorted(range(1, len(times)), key=lambda i: times[i])
    return [0, *rest]


def time_label(when: datetime) -> str:
    return when.strftime("%I:%M %p").lstrip("0").lower()


def sequence_slide(photo: LayoutPhoto, when: datetime, count: int, position: int) -> Image.Image:
    """The photo as an opening slide with the time it was taken and a row of dots, one per photo in
    time order, the current one filled."""
    canvas = opening(photo)
    fade = Image.linear_gradient("L").resize((W, 260)).point(lambda v: int(v * 0.5))
    canvas.paste(Image.new("RGB", (W, 260), (0, 0, 0)), (0, H - 260), fade)
    draw = ImageDraw.Draw(canvas)
    face = font("serif", 44, "Medium")
    draw.text((60, H - 120), time_label(when), font=face, fill=WHITE)
    gap, r = 30, 7
    x = W - 60 - gap * (count - 1)
    for i in range(count):
        cx, cy = x + i * gap, H - 92
        if i == position:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=WHITE)
        else:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=WHITE, width=2)
    return canvas
