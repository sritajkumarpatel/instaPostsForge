"""Styled posts drawn by code: grids, gallery frames, paper posters, panoramas and story carousels.

The AI picks the photos and writes the few words that go on them (styles.py); this module only lays
them out. It never invents image content: every pixel of a photo is the photographer's own, cropped
or scaled. Every slide is 1080x1350 (4:5), the tallest shape Instagram's feed shows, so all slides of
one carousel share a shape.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from .backdrops import Backdrop, draw_swatches
from .processor import Focus, _to_jpeg, place

W, H = 1080, 1350
PAPER = (239, 233, 221)
# Paper tones a poster may be printed on: cream, oat, sage, mist blue and blush.
PAPER_TONES = ((239, 233, 221), (232, 226, 214), (226, 230, 218), (222, 228, 232), (240, 228, 222))
GALLERY = (246, 244, 239)
INK = (48, 43, 38)
SOFT_INK = (112, 102, 92)
WHITE = (255, 255, 255)


@dataclass
class Slide:
    image: Image.Image
    alt_text: str


@dataclass
class LayoutPhoto:
    """One photo ready to lay out: full resolution, upright, in sRGB."""

    image: Image.Image
    focus: Focus | None = None
    alt_text: str = ""


def load_photo(original: bytes) -> Image.Image:
    """The original photo upright and in sRGB, so photos from different phones sit together on one slide."""
    image = Image.open(io.BytesIO(original))
    icc = image.info.get("icc_profile")
    image = ImageOps.exif_transpose(image).convert("RGB")
    if icc:
        try:
            from PIL import ImageCms

            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            image = ImageCms.profileToProfile(image, source, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:  # an odd profile only costs some colour accuracy
            pass
    return image


def to_jpeg(slide: Image.Image, quality: int = 95) -> bytes:
    return _to_jpeg(slide.convert("RGB"), quality, subsampling=0)


# --- fonts and text -------------------------------------------------------------

FONT_FILES = {"serif": "PlayfairDisplay.ttf", "italic": "PlayfairDisplay-Italic.ttf", "hand": "Caveat.ttf", "small": "CormorantGaramond.ttf"}


def font(kind: str, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
    path = resources.files("instapostsforge.imaging").joinpath("fonts", FONT_FILES[kind])
    with resources.as_file(path) as file:
        loaded = ImageFont.truetype(str(file), size)
    if weight:
        try:
            loaded.set_variation_by_name(weight)
        except Exception:  # a font without that weight keeps its default
            pass
    return loaded


def text_width(draw: ImageDraw.ImageDraw, text: str, typeface: ImageFont.FreeTypeFont, spacing: int = 0) -> int:
    if spacing:
        return sum(text_width(draw, ch, typeface) for ch in text) + spacing * max(0, len(text) - 1)
    left, _, right, _ = draw.textbbox((0, 0), text, font=typeface)
    return right - left


def wrap(draw: ImageDraw.ImageDraw, text: str, typeface: ImageFont.FreeTypeFont, width: int, spacing: int = 0) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        trial = f"{lines[-1]} {word}" if lines else word
        if lines and text_width(draw, trial, typeface, spacing) <= width:
            lines[-1] = trial
        else:
            lines.append(word)
    return lines


def fitted(draw, text: str, kind: str, size: int, width: int, max_lines: int, spacing: int = 0, weight: str | None = None):
    """The largest font up to size whose wrapped text fits in width and max_lines, and the lines."""
    while True:
        typeface = font(kind, size, weight)
        lines = wrap(draw, text, typeface, width, spacing)
        if (len(lines) <= max_lines and all(text_width(draw, ln, typeface, spacing) <= width for ln in lines)) or size <= 18:
            return typeface, lines
        size -= 2


def draw_spaced(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, typeface, fill, spacing: int) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=typeface, fill=fill)
        x += text_width(draw, ch, typeface) + spacing


def line_height(typeface: ImageFont.FreeTypeFont) -> int:
    ascent, descent = typeface.getmetrics()
    return ascent + descent


# --- photo placement -------------------------------------------------------------


def cover(photo: LayoutPhoto, size: tuple[int, int]) -> Image.Image:
    """The photo filling size exactly, trimmed where its subject isn't."""
    image, (w, h) = photo.image, size
    scale = max(w / image.width, h / image.height)
    scaled = image.resize((max(w, round(image.width * scale)), max(h, round(image.height * scale))), Image.LANCZOS)
    f = photo.focus
    left = place(scaled.width, w, f.left if f else None, f.right if f else None)
    top = place(scaled.height, h, f.top if f else None, f.bottom if f else None)
    return scaled.crop((left, top, left + w, top + h))


def contain(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    scale = min(box[0] / image.width, box[1] / image.height)
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)


def shadow(canvas: Image.Image, box: tuple[int, int, int, int], blur: int = 18, offset: int = 10, strength: int = 90) -> None:
    """A soft drop shadow under a photo placed at box, as if it were a print lying on the paper."""
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rectangle((box[0] + offset // 2, box[1] + offset, box[2] + offset // 2, box[3] + offset), fill=strength)
    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    canvas.paste(Image.new("RGB", canvas.size, (30, 24, 18)), (0, 0), mask)


def paper(seed: int, tone: tuple[int, int, int] = PAPER) -> Image.Image:
    """Warm paper: a little grain and a soft vignette. The grain differs a little each time, as paper does."""
    small = Image.effect_noise((W // 2, H // 2), 14).resize((W, H), Image.BICUBIC)
    fibre = Image.effect_noise((W // 6, H // 6), 30).resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(6))
    grain = ImageChops.add(small, fibre, scale=2.0, offset=-64)
    base = Image.new("RGB", (W, H), tone)
    texture = Image.merge("RGB", [grain] * 3)
    canvas = ImageChops.blend(base, ImageChops.multiply(base, ImageOps.autocontrast(texture, cutoff=2).point(lambda v: 225 + v // 9)), 0.6)
    vignette = Image.radial_gradient("L").resize((W, H)).point(lambda v: int(v * 0.22))
    canvas.paste(Image.new("RGB", (W, H), (120, 100, 80)), (0, 0), vignette)
    return canvas


def decor(decor_dir: str | None, seed: int) -> Image.Image | None:
    """An optional decoration (leaves, a sprig, tape) from the owner's own PNGs with transparency.
    None are bundled; without any the poster is plain paper."""
    if not decor_dir or not Path(decor_dir).is_dir():
        return None
    files = sorted(Path(decor_dir).glob("*.png"))
    if not files:
        return None
    try:
        return Image.open(random.Random(seed).choice(files)).convert("RGBA")
    except Exception:
        return None


# --- styles -------------------------------------------------------------------------


def grid(photos: list[LayoutPhoto], title: str | None = None) -> Image.Image:
    """2, 3, 4, 6 or 9 photos tiled edge to edge, with an optional line of text across the middle."""
    n = len(photos)
    canvas = Image.new("RGB", (W, H), WHITE)
    if n == 2:
        cells = [(0, 0, W // 2, H), (W // 2, 0, W, H)]
    elif n == 3:  # one tall photo on the left, two stacked on the right
        cells = [(0, 0, W * 3 // 5, H), (W * 3 // 5, 0, W, H // 2), (W * 3 // 5, H // 2, W, H)]
    else:
        cols, rows = {4: (2, 2), 6: (2, 3), 9: (3, 3)}[n]
        xs = [round(W * i / cols) for i in range(cols + 1)]
        ys = [round(H * j / rows) for j in range(rows + 1)]
        cells = [(xs[i], ys[j], xs[i + 1], ys[j + 1]) for j in range(rows) for i in range(cols)]
    for photo, (x0, y0, x1, y1) in zip(photos, cells):
        canvas.paste(cover(photo, (x1 - x0, y1 - y0)), (x0, y0))
    if title:
        overlay_title(canvas, title, (H // 2) if n != 3 else round(H * 0.5))
    return canvas


def overlay_title(canvas: Image.Image, text: str, centre_y: int) -> None:
    """White serif text with a soft shadow, readable on any photo."""
    draw = ImageDraw.Draw(canvas)
    typeface, lines = fitted(draw, text, "serif", 92, W - 140, 2, weight="SemiBold")
    lh = line_height(typeface)
    top = centre_y - lh * len(lines) // 2
    glow = Image.new("L", canvas.size, 0)
    gdraw = ImageDraw.Draw(glow)
    for i, ln in enumerate(lines):
        x = (W - text_width(draw, ln, typeface)) // 2
        gdraw.text((x, top + i * lh), ln, font=typeface, fill=170)
    glow = glow.filter(ImageFilter.GaussianBlur(14))
    canvas.paste(Image.new("RGB", canvas.size, (0, 0, 0)), (0, 0), glow)
    for i, ln in enumerate(lines):
        x = (W - text_width(draw, ln, typeface)) // 2
        draw.text((x, top + i * lh), ln, font=typeface, fill=WHITE)


def gallery(photo: LayoutPhoto, title: str | None = None, detail: str | None = None, backdrop: Backdrop | None = None) -> Image.Image:
    """The photo on an off-white margin with a small museum-style label under it. The print takes as
    much of the slide as the label leaves, so the photo, not the margin, fills the frame. A backdrop
    replaces the plain margin and sets the label's ink."""
    canvas = backdrop.image.copy() if backdrop else Image.new("RGB", (W, H), GALLERY)
    ink, soft = (backdrop.ink, backdrop.soft) if backdrop else (INK, SOFT_INK)
    swatches = bool(backdrop and backdrop.swatches)
    label_space = (150 if (title or detail) else 0) + (60 if swatches else 0)
    print_ = contain(photo.image, (W - 120, H - 140 - label_space))
    x = (W - print_.width) // 2
    y = (H - label_space - print_.height) // 2 + (10 if label_space else 0)
    shadow(canvas, (x, y, x + print_.width, y + print_.height), blur=16, offset=8, strength=60)
    canvas.paste(print_, (x, y))
    draw = ImageDraw.Draw(canvas)
    ly = y + print_.height + 44
    if title:
        typeface, lines = fitted(draw, title, "serif", 44, W - 220, 1, weight="Medium")
        draw.text(((W - text_width(draw, lines[0], typeface)) // 2, ly), lines[0], font=typeface, fill=ink)
        ly += line_height(typeface) + 8
    if detail:
        small = font("italic", 28)
        draw.text(((W - text_width(draw, detail, small)) // 2, ly), detail, font=small, fill=soft)
        ly += line_height(small)
    if swatches:
        draw_swatches(canvas, backdrop.colours, (W // 2, ly + 34), 15)
    return canvas


# --- poster: several layouts, picked to suit the photo's shape -------------------------

TITLE_SPACING = 9


def _title_lines(draw, title: str, width: int, size: int = 36):
    """A poster title in spaced capitals. "Small towns, big dreams" sets as two lines, one phrase each."""
    upper = title.upper()
    phrases = [p.strip() for p in upper.split(",") if p.strip()]
    if len(phrases) == 2:
        typeface = min((fitted(draw, p, "serif", size, width, 1, spacing=TITLE_SPACING)[0] for p in phrases), key=lambda f: f.size)
        return typeface, phrases
    return fitted(draw, upper.replace(",", ""), "serif", size, width, 2, spacing=TITLE_SPACING)


def text_block_height(title: str | None, line: str | None, width: int) -> int:
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    height = 0
    if title:
        typeface, lines = _title_lines(draw, title, width)
        height += (line_height(typeface) + 2) * len(lines) + 44
    if line:
        typeface, lines = fitted(draw, line, "italic", 28, width, 3)
        height += (line_height(typeface) + 4) * len(lines)
    return height


def draw_text_block(
    canvas: Image.Image, x: int, y: int, width: int, title: str | None, line: str | None, align: str = "left", ink=INK, soft=SOFT_INK
) -> int:
    """The spaced title, a short rule and the italic line, from (x, y); returns where it ends."""
    draw = ImageDraw.Draw(canvas)

    def left_for(w: int) -> int:
        return x + (width - w) // 2 if align == "center" else x + width - w if align == "right" else x

    if title:
        typeface, lines = _title_lines(draw, title, width)
        for ln in lines:
            draw_spaced(draw, (left_for(text_width(draw, ln, typeface, TITLE_SPACING)), y), ln, typeface, ink, TITLE_SPACING)
            y += line_height(typeface) + 2
        y += 18
        rx = left_for(80)
        draw.line((rx, y, rx + 80, y), fill=soft, width=2)
        y += 26
    if line:
        typeface, lines = fitted(draw, line, "italic", 28, width, 3)
        for ln in lines:
            draw.text((left_for(text_width(draw, ln, typeface)), y), ln, font=typeface, fill=soft)
            y += line_height(typeface) + 4
    return y


def draw_note(canvas: Image.Image, note: str, box: tuple[int, int, int, int], angle: float, colour=SOFT_INK) -> None:
    """A handwritten aside, underlined and slightly tilted, fitted inside box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    width = max(160, x1 - x0)
    layer = Image.new("L", (width, max(140, y1 - y0)), 0)
    ldraw = ImageDraw.Draw(layer)
    hand, lines = fitted(ldraw, note, "hand", 54, width - 30, 3)
    ny = 8
    for ln in lines:
        ldraw.text((12, ny), ln, font=hand, fill=255)
        ny += line_height(hand) - 8
    ldraw.line((16, ny + 14, 16 + min(width - 30, text_width(ldraw, lines[-1], hand)), ny + 6), fill=255, width=3)
    layer = layer.crop((0, 0, width, min(layer.height, ny + 30))).rotate(angle, resample=Image.BICUBIC, expand=True)
    nx = min(max(0, x0), W - layer.width)
    ny = min(max(0, y0), H - layer.height)
    canvas.paste(Image.new("RGB", layer.size, colour), (nx, ny), layer)


def place_print(canvas: Image.Image, image: Image.Image, xy: tuple[int, int], rng: random.Random, finish: str) -> tuple[int, int, int, int]:
    """Lays the photo on the paper as a print: plain, with a white border, or taped down, sometimes
    with the slightest tilt. Returns the box it covers."""
    x, y = xy
    if finish in ("border", "taped_border"):
        edge = 16
        bordered = Image.new("RGB", (image.width + edge * 2, image.height + edge * 2), (250, 248, 243))
        bordered.paste(image, (edge, edge))
        image, x, y = bordered, x - edge, y - edge
    tilt = rng.choice((-1.2, -0.8, 0.8, 1.2)) if finish == "border" and rng.random() < 0.5 else 0
    box = (x, y, x + image.width, y + image.height)
    shadow(canvas, box)
    if tilt:
        rotated = image.convert("RGBA").rotate(tilt, resample=Image.BICUBIC, expand=True)
        canvas.paste(rotated, (x - (rotated.width - image.width) // 2, y - (rotated.height - image.height) // 2), rotated)
    else:
        canvas.paste(image, (x, y))
    if finish.startswith("taped"):
        for cx, angle in ((box[0] + 40, rng.uniform(-30, -18)), (box[2] - 40, rng.uniform(18, 30))):
            tape = Image.new("RGBA", (150, 44), (236, 226, 200, 170)).rotate(angle, resample=Image.BICUBIC, expand=True)
            canvas.paste(tape, (cx - tape.width // 2, box[1] - tape.height // 2), tape)
    return box


POSTER_LAYOUTS = {
    "landscape": ("wide_top", "wide_low", "wide_band"),
    "portrait": ("tall_left", "tall_right", "tall_centre"),
    "square": ("square_top", "square_left"),
}


KEEP_BAND = ("kraft", "linen", "handmade", "slate", "navy", "newsprint", "palette")


def poster_layouts(shape: float) -> tuple[str, ...]:
    kind = "landscape" if shape >= 1.15 else "portrait" if shape < 0.9 else "square"
    return POSTER_LAYOUTS[kind]


def poster(
    photo: LayoutPhoto,
    title: str | None = None,
    line: str | None = None,
    note: str | None = None,
    *,
    seed: int = 0,
    decor_dir: str | None = None,
    layout: str | None = None,
    finish: str | None = None,
    backdrop: Backdrop | None = None,
) -> Image.Image:
    """The photo as a large print on warm paper, with a spaced serif title, a quiet italic line and a
    handwritten note. The layout suits the photo's shape (a wide photo spans the page, a tall one
    stands to one side of the note) and, with the print's finish, changes from post to post: the seed
    picks them, so the same post always looks the same and the next one looks different."""
    rng = random.Random(seed)
    tone = rng.choice(PAPER_TONES)
    canvas = backdrop.image.copy() if backdrop else paper(seed, tone)
    ink, soft = (backdrop.ink, backdrop.soft) if backdrop else (INK, SOFT_INK)
    shape = photo.image.width / photo.image.height
    choices = poster_layouts(shape)
    if backdrop and backdrop.kind not in ("paper", *KEEP_BAND) and len(choices) > 1:
        choices = tuple(c for c in choices if c != "wide_band")  # a full-width band would hide the background
    layout = layout or rng.choice(choices)
    finish = finish or rng.choice(("plain", "border", "taped", "taped_border"))
    margin = 70
    text_w = 600
    text_h = text_block_height(title, line, text_w)
    gap = 56 if text_h else 0
    angle = rng.choice((-7, -5, 5, 7))

    if layout in ("wide_top", "wide_low", "wide_band", "square_top", "square_left"):
        full = layout == "wide_band"
        note_room = 130 if note and layout != "square_left" else 0
        box_w = W if full else W - 2 * margin - (150 if layout == "square_left" and note else 0)
        box_h = H - 2 * margin - text_h - gap - note_room
        print_ = contain(photo.image, (box_w, box_h)) if not full else cover(photo, (W, min(box_h, round(W / max(shape, 1.0)))))
        px = 0 if full else margin if layout == "square_left" else (W - print_.width) // 2
        block = print_.height + gap + text_h + note_room
        top = max(margin, (H - block) // 2)
        if layout == "wide_low":  # words above, the photo below them
            draw_text_block(canvas, margin + 10, top, text_w, title, line, ink=ink, soft=soft)
            py = top + text_h + gap
            box = place_print(canvas, print_, (px, py), rng, "plain" if full else finish)
            if note:
                draw_note(canvas, note, (W - 400, box[3] + 16, W - margin, box[3] + 16 + note_room + 40), angle, soft)
        else:  # the photo first, the words under it
            py = top
            box = place_print(canvas, print_, (px, py), rng, "plain" if full else finish)
            ty = box[3] + gap
            if layout == "square_left" and note:
                draw_note(canvas, note, (box[2] + 12, py + 40, W - 8, py + 360), angle, soft)
            elif note:
                draw_note(canvas, note, (W - 380, max(box[3] + 10, ty + text_h - 120), W - margin, ty + text_h + note_room), angle, soft)
            draw_text_block(canvas, margin + 10, ty, text_w if note else W - 2 * margin, title, line, ink=ink, soft=soft)
    else:  # tall photos stand to one side, with the note in the column beside them
        note_col = 250 if note else 0
        box_w = W - 2 * margin - note_col
        box_h = H - 2 * margin - text_h - gap
        print_ = contain(photo.image, (box_w if layout != "tall_centre" else W - 2 * margin, box_h))
        if layout == "tall_centre":
            px = (W - print_.width) // 2
        elif layout == "tall_left":
            px = margin
        else:
            px = W - margin - print_.width
        block = print_.height + gap + text_h
        py = max(margin, (H - block) // 2)
        box = place_print(canvas, print_, (px, py), rng, finish)
        if note:
            if layout == "tall_left":
                draw_note(canvas, note, (box[2] + 24, py + 50, W - 16, py + 400), angle, soft)
            elif layout == "tall_right":
                draw_note(canvas, note, (16, py + 50, box[0] - 24, py + 400), -abs(angle), soft)
            else:
                draw_note(canvas, note, (W - 400, box[3] + 10, W - margin, box[3] + 200), angle, soft)
        align = "right" if layout == "tall_right" else "left"
        draw_text_block(canvas, margin + 10, box[3] + gap, W - 2 * margin - 20 - (360 if layout == "tall_centre" and note else 0), title, line, align, ink=ink, soft=soft)

    if backdrop and backdrop.swatches:
        draw_swatches(canvas, backdrop.colours, (W // 2, H - 40), 13)
    leaf = decor(decor_dir, seed)
    if leaf is not None:
        leaf = contain(leaf, (380, 380))
        canvas.paste(leaf, (0, 0), leaf)
    return canvas


def panorama(photo: LayoutPhoto) -> list[Image.Image] | None:
    """A wide photo cut into 2 or 3 slides that join up as you swipe, or None if it isn't wide enough."""
    ratio = photo.image.width / photo.image.height
    n = min(3, int(math.floor(ratio * H / W + 0.25)))
    if n < 2:
        return None
    strip = cover(photo, (W * n, H))
    return [strip.crop((W * i, 0, W * (i + 1), H)) for i in range(n)]


def title_over(photo: LayoutPhoto, title: str | None) -> Image.Image:
    """A full-bleed cover with the title over a soft dark fade at the bottom."""
    canvas = cover(photo, (W, H))
    if not title:
        return canvas
    fade = Image.linear_gradient("L").resize((W, H // 2)).point(lambda v: int(v * 0.65))
    canvas.paste(Image.new("RGB", (W, H // 2), (0, 0, 0)), (0, H // 2), fade)
    draw = ImageDraw.Draw(canvas)
    typeface, lines = fitted(draw, title, "serif", 84, W - 160, 2, weight="SemiBold")
    lh = line_height(typeface)
    y = H - 130 - lh * len(lines)
    for ln in lines:
        draw.text((80, y), ln, font=typeface, fill=WHITE)
        y += lh
    return canvas


def closing(line: str, seed: int = 0) -> Image.Image:
    """A paper slide with one line of text, to end a story carousel."""
    canvas = paper(seed + 1)
    draw = ImageDraw.Draw(canvas)
    typeface, lines = fitted(draw, line, "italic", 56, W - 240, 4)
    lh = line_height(typeface) + 8
    y = (H - lh * len(lines)) // 2
    for ln in lines:
        draw.text(((W - text_width(draw, ln, typeface)) // 2, y), ln, font=typeface, fill=INK)
        y += lh
    draw.line((W // 2 - 40, y + 30, W // 2 + 40, y + 30), fill=SOFT_INK, width=2)
    return canvas
