"""Styled posts drawn by code: grids, panoramas and story carousels, and the helpers every layout shares.

The AI picks the photos and writes the few words that go on them (styles.py); this module only lays
them out. It never invents image content: every pixel of a photo is the photographer's own, cropped
or scaled. Every slide is 1080x1350 (4:5), the tallest shape Instagram's feed shows, so all slides of
one carousel share a shape.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from importlib import resources

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from .processor import Focus, _to_jpeg, place

W, H = 1080, 1350
PAPER = (239, 233, 221)
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


# --- spaced titles (the film strip) --------------------------------------------------

TITLE_SPACING = 9


def _title_lines(draw, title: str, width: int, size: int = 36):
    """A poster title in spaced capitals. "Small towns, big dreams" sets as two lines, one phrase each."""
    upper = title.upper()
    phrases = [p.strip() for p in upper.split(",") if p.strip()]
    if len(phrases) == 2:
        typeface = min((fitted(draw, p, "serif", size, width, 1, spacing=TITLE_SPACING)[0] for p in phrases), key=lambda f: f.size)
        return typeface, phrases
    return fitted(draw, upper.replace(",", ""), "serif", size, width, 2, spacing=TITLE_SPACING)


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
