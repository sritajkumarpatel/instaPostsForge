"""Frames around a print: a film strip and a polaroid stack. (Postcard, ticket, magazine and festive
frames were retired in November 2026: they added decoration, not something to look at.)

Each one lays the photographer's photo, scaled and never altered, inside a drawn frame. The words on
a frame come from the caption writer (checked like the caption) or from code (place and month). No
frame copies a real brand: no mastheads, airline or rail logos, or real stamps.
"""

from __future__ import annotations

import random

from PIL import Image, ImageDraw, ImageFilter

from .backdrops import Backdrop, backdrop as make_backdrop
from .layouts import (
    INK,
    SOFT_INK,
    TITLE_SPACING,
    LayoutPhoto,
    _title_lines,
    contain,
    draw_spaced,
    fitted,
    font,
    line_height,
    text_width,
)

W, H = 1080, 1350
FRAMES = ("film", "polaroids")
CARD = (250, 247, 240)
MARIGOLD = (236, 160, 38)
MAROON = (128, 36, 44)
GOLD = (196, 150, 60)
STAMP_ORANGE = (232, 120, 40)


def _centered(draw, y: int, text: str, typeface, fill, x0: int = 0, x1: int = W, spacing: int = 0) -> None:
    w = text_width(draw, text, typeface, spacing)
    if spacing:
        draw_spaced(draw, ((x0 + x1 - w) // 2, y), text, typeface, fill, spacing)
    else:
        draw.text(((x0 + x1 - w) // 2, y), text, font=typeface, fill=fill)


def film(photo: LayoutPhoto, title: str | None, frame_no: int, *, seed: int = 0) -> Image.Image:
    """The photo on a strip of film: sprocket holes down both sides, a frame number, and the title
    under it in small white capitals."""
    canvas = Image.new("RGB", (W, H), (18, 17, 16))
    draw = ImageDraw.Draw(canvas)
    edge = 92
    for x0 in (22, W - 22 - 48):
        for y in range(18, H, 74):
            draw.rounded_rectangle((x0, y, x0 + 48, y + 40), radius=8, fill=(232, 228, 220))
    label_space = 110 if title else 40
    print_ = contain(photo.image, (W - 2 * edge - 24, H - 120 - label_space))
    px = (W - print_.width) // 2
    py = (H - label_space - print_.height) // 2 + 20
    canvas.paste(print_, (px, py))
    numbers = font("serif", 22, "SemiBold")
    draw.text((px, py - 34), f"{frame_no}", font=numbers, fill=STAMP_ORANGE)
    draw.text((px + 70, py - 34), f"{frame_no}A", font=numbers, fill=STAMP_ORANGE)
    if title:
        typeface, lines = _title_lines(draw, title, W - 2 * edge - 60, 30)
        y = py + print_.height + 36
        for ln in lines[:2]:
            _centered(draw, y, ln, typeface, (236, 232, 224), spacing=TITLE_SPACING)
            y += line_height(typeface)
    return canvas


def polaroids(photos: list[LayoutPhoto], title: str | None, *, seed: int = 0, back: Backdrop | None = None) -> Image.Image:
    """Two to four instant prints scattered on paper, each slightly turned, with the title handwritten
    underneath. Each print shows the whole photo, scaled to fit, with the wide white foot of an instant print."""
    rng = random.Random(seed)
    back = back or make_backdrop("kraft", photos[0].image, seed)
    canvas = back.image.copy()
    n = len(photos)
    size = {2: 520, 3: 470, 4: 430}.get(n, 430)
    spots = {
        2: [(300, 420), (760, 760)],
        3: [(300, 380), (770, 480), (520, 850)],
        4: [(290, 360), (790, 380), (300, 830), (790, 850)],
    }[min(max(n, 2), 4)]
    for photo, (cx, cy) in zip(photos, spots):
        pic = contain(photo.image, (size, size))
        frame = Image.new("RGB", (pic.width + 40, pic.height + 120), (252, 251, 247))
        frame.paste(pic, (20, 20))
        angle = rng.uniform(-7, 7)
        turned = frame.convert("RGBA").rotate(angle, resample=Image.BICUBIC, expand=True)
        x, y = cx - turned.width // 2, cy - turned.height // 2
        shade = Image.new("L", canvas.size, 0)
        shade.paste(turned.getchannel("A").point(lambda a: 80 if a else 0), (x + 8, y + 14))
        canvas.paste(Image.new("RGB", canvas.size, (30, 24, 18)), (0, 0), shade.filter(ImageFilter.GaussianBlur(14)))
        canvas.paste(turned, (x, y), turned)
    if title:
        draw = ImageDraw.Draw(canvas)
        hand, lines = fitted(draw, title, "hand", 74, W - 200, 2)
        y = H - 90 - line_height(hand) * len(lines)
        for ln in lines:
            _centered(draw, y, ln, hand, back.ink)
            y += line_height(hand) - 10
    return canvas


__all__ = ["FRAMES", "film", "polaroids", "INK", "SOFT_INK"]
