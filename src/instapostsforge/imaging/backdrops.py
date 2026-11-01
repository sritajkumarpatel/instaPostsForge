"""Backgrounds behind a poster or gallery print, drawn from the photo's own colours or a plain texture.

Nothing here touches the photo: the print is pasted on top, as it is. The "echo" background is the
photo itself, enlarged and blurred, behind the sharp print. Every background reports which ink reads
on it, so text never disappears into it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from . import palette as pal

W, H = 1080, 1350
RGB = tuple[int, int, int]

# Every background a poster or gallery slide can have. "paper" is the warm paper posters always had.
BACKGROUNDS = ("paper", "palette", "echo", "swatches", "two_tone", "sun", "kraft", "linen", "handmade", "slate", "navy", "newsprint")
TEXTURES = {  # name: (base colour, grain strength, vignette strength)
    "kraft": ((196, 166, 128), 1.4, 0.26),
    "linen": ((236, 232, 222), 0.7, 0.14),
    "handmade": ((240, 234, 222), 1.2, 0.18),
    "slate": ((50, 54, 58), 0.9, 0.30),
    "navy": ((24, 32, 54), 0.7, 0.30),
    "newsprint": ((232, 229, 220), 0.5, 0.10),
}
PAPER_TONES = ((239, 233, 221), (232, 226, 214), (226, 230, 218), (222, 228, 232), (240, 228, 222))


@dataclass
class Backdrop:
    image: Image.Image
    ink: RGB  # for titles
    soft: RGB  # for the quiet line and labels
    accent: RGB  # for small marks such as a rule or swatch outline
    colours: list[RGB] = field(default_factory=list)  # the photo's palette, for swatches
    swatches: bool = False  # whether the layout should draw the palette as a row of dots
    kind: str = "paper"


def _textured(tone: RGB, seed: int, grain: float = 1.0, vignette: float = 0.22) -> Image.Image:
    """A flat tone with a little grain and fibre, and a soft vignette, like real paper or card."""
    rng = random.Random(seed)
    small = Image.effect_noise((W // 2, H // 2), 14 * grain).resize((W, H), Image.BICUBIC)
    fibre = Image.effect_noise((W // 6 + rng.randint(0, 9), H // 6 + rng.randint(0, 9)), 30 * grain)
    fibre = fibre.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(6))
    grain_img = ImageChops.add(small, fibre, scale=2.0, offset=-64)
    base = Image.new("RGB", (W, H), tone)
    texture = Image.merge("RGB", [grain_img] * 3)
    canvas = ImageChops.blend(base, ImageChops.multiply(base, ImageOps.autocontrast(texture, cutoff=2).point(lambda v: 225 + v // 9)), 0.6)
    shade = Image.radial_gradient("L").resize((W, H)).point(lambda v: int(v * vignette))
    canvas.paste(Image.new("RGB", (W, H), pal.mix(tone, (0, 0, 0), 0.5)), (0, 0), shade)
    return canvas


def _linen(canvas: Image.Image, seed: int) -> None:
    """Fine crossing threads, faint."""
    rng = random.Random(seed)
    layer = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(layer)
    for y in range(0, H, 4):
        draw.line((0, y, W, y), fill=rng.randint(6, 16))
    for x in range(0, W, 4):
        draw.line((x, 0, x, H), fill=rng.randint(4, 12))
    canvas.paste(Image.new("RGB", (W, H), (120, 110, 96)), (0, 0), layer)


def _newsprint(canvas: Image.Image) -> None:
    """A faint halftone of dots, as on newspaper."""
    layer = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(layer)
    for y in range(0, H, 9):
        for x in range((y // 9) % 2 * 4, W, 9):
            draw.ellipse((x, y, x + 2, y + 2), fill=22)
    canvas.paste(Image.new("RGB", (W, H), (60, 60, 60)), (0, 0), layer)


def _specks(canvas: Image.Image, seed: int) -> None:
    """A few faint specks of light, for a night-sky navy."""
    rng = random.Random(seed)
    draw = ImageDraw.Draw(canvas)
    for _ in range(140):
        x, y, r = rng.randrange(W), rng.randrange(H), rng.choice((1, 1, 1, 2))
        v = rng.randint(70, 140)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(v, v, min(255, v + 30)))


def _readable(bg: RGB, colours: list[RGB]) -> tuple[RGB, RGB, RGB]:
    ink = pal.ink_for(bg, pal.INK)
    soft = pal.mix(ink, bg, 0.35)
    if pal.contrast(soft, bg) < 4.5:
        soft = ink
    return ink, soft, pal.accent(colours, bg)


def backdrop(kind: str, photo: Image.Image, seed: int = 0, colours: list[RGB] | None = None) -> Backdrop:
    """The background named by kind, matched to photo. An unknown kind gives plain paper."""
    rng = random.Random(seed)
    colours = colours or pal.palette(photo)
    main = colours[0]
    if kind == "echo":
        scale = max(W / photo.width, H / photo.height)
        big = photo.convert("RGB").resize((round(photo.width * scale) + 2, round(photo.height * scale) + 2), Image.BILINEAR)
        left, top = (big.width - W) // 2, (big.height - H) // 2
        image = big.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(48))
        image = ImageChops.blend(image, Image.new("RGB", (W, H), (0, 0, 0)), 0.42)
        bg = image.resize((1, 1), Image.BOX).getpixel((0, 0))
        ink = pal.ink_for(bg, pal.WHITE)
        return Backdrop(image, ink, ink, pal.accent(colours, bg), colours, False, kind)
    if kind in TEXTURES:
        tone, grain, vignette = TEXTURES[kind]
        image = _textured(tone, seed, grain, vignette)
        if kind == "linen":
            _linen(image, seed)
        elif kind == "newsprint":
            _newsprint(image)
        elif kind == "navy":
            _specks(image, seed)
        return Backdrop(image, *_readable(tone, colours), colours, False, kind)
    if kind == "two_tone":
        pairs = [(a, b) for a in colours[:3] for b in colours[:4] if a != b and not pal.looks_like_flag([a, b])]
        top_c, low_c = pairs[0] if pairs else (main, main)
        upper, lower = pal.mix(pal.soft_tint(top_c, 0.72), top_c, 0.25), pal.soft_tint(low_c)
        if pal.contrast(pal.ink_for(lower), upper) < 4.5:  # text may cross the seam: both halves must take the same ink
            upper = pal.soft_tint(top_c, 0.78)
        if pal.looks_like_flag([upper, lower]):
            upper = lower = pal.soft_tint(main)
        image = _textured(lower, seed, 0.6, 0.12)
        seam = round(H * rng.uniform(0.5, 0.6))
        image.paste(_textured(upper, seed + 1, 0.6, 0.12).crop((0, 0, W, seam)), (0, 0))
        return Backdrop(image, *_readable(lower, colours), colours, False, kind)
    if kind in ("palette", "swatches", "sun"):
        tone = pal.soft_tint(main)
        image = _textured(tone, seed, 0.8, 0.16)
        if kind == "sun":
            sun = pal.mix(pal.warmest(colours), tone, 0.45)
            # low on the page, where the margin and the words are, so it shows beside the print
            r = rng.randint(260, 330)
            cx, cy = rng.choice((200, W - 200)) + rng.randint(-60, 60), rng.randint(950, 1120)
            glow = Image.new("L", (W, H), 0)
            ImageDraw.Draw(glow).ellipse((cx - r, cy - r, cx + r, cy + r), fill=235)
            image.paste(Image.new("RGB", (W, H), sun), (0, 0), glow.filter(ImageFilter.GaussianBlur(3)))
        swatches = pal.safe_swatches(colours) if kind == "swatches" else []
        return Backdrop(image, *_readable(tone, colours), swatches or colours, len(swatches) >= 3, kind)
    tone = rng.choice(PAPER_TONES)
    return Backdrop(_textured(tone, seed), *_readable(tone, colours), colours, False, "paper")


def draw_swatches(canvas: Image.Image, colours: list[RGB], centre: tuple[int, int], radius: int = 18, outline: RGB | None = None) -> None:
    """The photo's palette as a row of dots, like a paint card."""
    colours = pal.safe_swatches(colours)[:5]
    if len(colours) < 3:
        return
    draw = ImageDraw.Draw(canvas)
    step = radius * 3
    x = centre[0] - step * (len(colours) - 1) // 2
    for c in colours:
        draw.ellipse((x - radius, centre[1] - radius, x + radius, centre[1] + radius), fill=c, outline=outline, width=2 if outline else 0)
        x += step
