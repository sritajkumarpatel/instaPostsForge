"""Frames around a print: postcard, ticket, film strip, magazine page, festive border and a polaroid stack.

Each one lays the photographer's photo, scaled and never altered, inside a drawn frame. The words on
a frame come from the caption writer (checked like the caption) or from code (place and month). No
frame copies a real brand: no mastheads, airline or rail logos, or real stamps.
"""

from __future__ import annotations

import random

from PIL import Image, ImageDraw, ImageFilter

from . import palette as pal
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
    shadow,
    text_width,
)

W, H = 1080, 1350
FRAMES = ("postcard", "ticket", "film", "magazine", "festive")
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


def postcard(photo: LayoutPhoto, line: str | None, place: str | None, month: str | None, *, seed: int = 0, back: Backdrop | None = None) -> Image.Image:
    """A postcard lying on the table: the photo on the front half, a handwritten line on the left of
    the back half, and a stamp with a postmark (place and month) on the right. The stamp is a plain
    coloured block with a sun, never a real stamp."""
    rng = random.Random(seed)
    back = back or make_backdrop("linen", photo.image, seed)
    canvas = back.image.copy()
    margin = 50
    card = (margin, margin + 10, W - margin, H - margin - 10)
    shadow(canvas, card, blur=20, offset=12, strength=80)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(card, fill=CARD)
    inner = 26
    lower_h = 330
    print_ = contain(photo.image, (card[2] - card[0] - 2 * inner, card[3] - card[1] - 2 * inner - lower_h))
    px = (W - print_.width) // 2
    py = card[1] + inner + (card[3] - card[1] - 2 * inner - lower_h - print_.height) // 2
    canvas.paste(print_, (px, py))
    top = card[3] - lower_h
    mid = (card[0] + card[2]) // 2 + 60
    draw.line((mid, top + 40, mid, card[3] - 40), fill=(205, 198, 186), width=2)
    if line:
        hand, lines = fitted(draw, line, "hand", 50, mid - card[0] - 80, 4)
        y = top + 50
        for ln in lines:
            draw.text((card[0] + 40, y), ln, font=hand, fill=(52, 64, 96))
            y += line_height(hand) - 6
    # stamp
    colours = back.colours or [STAMP_ORANGE]
    stamp_fill = pal.mix(pal.accent(colours, CARD), CARD, 0.15)
    sx1, sy0 = card[2] - 40, top + 40
    sx0, sy1 = sx1 - 130, sy0 + 160
    for x in range(sx0, sx1 + 1, 14):
        draw.ellipse((x - 5, sy0 - 5, x + 5, sy0 + 5), fill=CARD)
        draw.ellipse((x - 5, sy1 - 5, x + 5, sy1 + 5), fill=CARD)
    draw.rectangle((sx0 + 6, sy0 + 6, sx1 - 6, sy1 - 6), fill=stamp_fill)
    sun = pal.ink_for(stamp_fill)
    draw.ellipse(((sx0 + sx1) // 2 - 26, sy0 + 40, (sx0 + sx1) // 2 + 26, sy0 + 92), outline=sun, width=4)
    draw.line((sx0 + 20, sy1 - 40, sx1 - 20, sy1 - 40), fill=sun, width=3)
    # postmark: two rings with the place and month, slightly off the stamp
    cx, cy, r = sx0 - 40, sy0 + 120, 88
    mark = (70, 70, 80)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=mark, width=3)
    draw.ellipse((cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12), outline=mark, width=2)
    small = font("serif", 20, "Medium")
    for i, text in enumerate([t for t in ((place or "")[:18].upper(), (month or "").upper()) if t]):
        _centered(draw, cy - 26 + i * 30, text, small, mark, cx - r, cx + r)
    for i in range(4):  # wavy cancel lines
        y = cy - 30 + i * 20
        pts = [(cx + r - 6 + k * 12, y + (4 if k % 2 else -4)) for k in range(9)]
        draw.line(pts, fill=mark, width=2)
    # address lines stay empty: never an address
    for i in range(2):
        y = card[3] - 120 + i * 50
        draw.line((mid + 30, y, card[2] - 40, y), fill=(205, 198, 186), width=2)
    del rng
    return canvas


def ticket(photo: LayoutPhoto, title: str | None, place: str | None, month: str | None, number: int, *, seed: int = 0, back: Backdrop | None = None) -> Image.Image:
    """A ticket stub: the photo on the ticket's face, a perforated tear line, and a stub with a spaced
    title and plain fields (place, month, number). No carrier names or logos."""
    back = back or make_backdrop("slate", photo.image, seed)
    canvas = back.image.copy()
    margin = 70
    stub_h = 300
    box = (margin, margin, W - margin, H - margin)
    shadow(canvas, box, blur=22, offset=14, strength=110)
    mask = Image.new("L", (W, H), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle(box, radius=34, fill=255)
    tear = box[3] - stub_h
    for cx in (box[0], box[2]):
        mdraw.ellipse((cx - 30, tear - 30, cx + 30, tear + 30), fill=0)
    card_colour = pal.soft_tint(back.colours[0] if back.colours else CARD, 0.93)
    canvas.paste(Image.new("RGB", (W, H), card_colour), (0, 0), mask)
    draw = ImageDraw.Draw(canvas)
    inner = 34
    print_ = contain(photo.image, (box[2] - box[0] - 2 * inner, tear - box[1] - 2 * inner))
    canvas.paste(print_, ((W - print_.width) // 2, box[1] + inner + (tear - box[1] - 2 * inner - print_.height) // 2))
    for x in range(box[0] + 40, box[2] - 40, 22):
        draw.line((x, tear, x + 10, tear), fill=(150, 144, 134), width=3)
    ink = pal.ink_for(card_colour)
    soft = pal.mix(ink, card_colour, 0.3) if pal.contrast(pal.mix(ink, card_colour, 0.3), card_colour) >= 4.5 else ink
    y = tear + 44
    if title:
        typeface, lines = _title_lines(draw, title, box[2] - box[0] - 120, 40)
        for ln in lines[:2]:
            _centered(draw, y, ln, typeface, ink, spacing=TITLE_SPACING)
            y += line_height(typeface) + 2
    fields = [("PLACE", (place or "")[:20]), ("MONTH", month or ""), ("NO.", f"{number:04d}")]
    fields = [f for f in fields if f[1]]
    label, value = font("serif", 18, "SemiBold"), font("serif", 30, "Medium")
    col_w = (box[2] - box[0] - 80) // max(1, len(fields))
    for i, (k, v) in enumerate(fields):
        x0 = box[0] + 40 + i * col_w
        _centered(draw, box[3] - 120, k, label, soft, x0, x0 + col_w, spacing=4)
        _centered(draw, box[3] - 90, v, value, ink, x0, x0 + col_w)
    return canvas


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


def magazine(photo: LayoutPhoto, title: str | None, line: str | None, issue: str, page: int, *, seed: int = 0, back: Backdrop | None = None) -> Image.Image:
    """A magazine page: a small issue line and page number over a thin rule, the photo large, then a
    serif headline and a short standfirst. The issue line is generic ("Vol. 11 · November 2026"),
    never a real magazine's name."""
    back = back or make_backdrop("newsprint", photo.image, seed)
    canvas = back.image.copy()
    draw = ImageDraw.Draw(canvas)
    margin = 64
    small = font("serif", 22, "SemiBold")
    draw_spaced(draw, (margin, 52), issue.upper(), small, back.soft, 3)
    num = str(page)
    draw.text((W - margin - text_width(draw, num, small), 52), num, font=small, fill=back.soft)
    draw.line((margin, 92, W - margin, 92), fill=back.ink, width=2)
    head_h = 0
    if title:
        head_font, head_lines = fitted(draw, title, "serif", 76, W - 2 * margin, 2, weight="SemiBold")
        head_h = (line_height(head_font)) * len(head_lines) + 20
    stand_h = 0
    if line:
        stand_font, stand_lines = fitted(draw, line, "italic", 30, W - 2 * margin, 2)
        stand_h = (line_height(stand_font) + 4) * len(stand_lines) + 10
    print_ = contain(photo.image, (W - 2 * margin, H - 130 - 70 - head_h - stand_h))
    px, py = (W - print_.width) // 2, 122
    canvas.paste(print_, (px, py))
    y = py + print_.height + 30
    if title:
        for ln in head_lines:
            draw.text((margin, y), ln, font=head_font, fill=back.ink)
            y += line_height(head_font)
        y += 20
    if line:
        for ln in stand_lines:
            draw.text((margin, y), ln, font=stand_font, fill=back.soft)
            y += line_height(stand_font) + 4
    return canvas


def festive(photo: LayoutPhoto, title: str | None, detail: str | None, *, seed: int = 0, back: Backdrop | None = None) -> Image.Image:
    """A gallery print inside a soft festive border of marigold and maroon dots. Colours only: no
    deity, symbol or religious sign is ever drawn."""
    back = back or make_backdrop("palette", photo.image, seed)
    canvas = back.image.copy()
    draw = ImageDraw.Draw(canvas)
    edge = 34
    draw.rectangle((edge, edge, W - edge, H - edge), outline=GOLD, width=3)
    draw.rectangle((edge + 14, edge + 14, W - edge - 14, H - edge - 14), outline=GOLD, width=1)
    step = 34
    k = 0
    for x in range(edge + 30, W - edge - 20, step):
        for y in (edge + 30, H - edge - 30):
            c = MARIGOLD if k % 2 else MAROON
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=c)
            k += 1
    for y in range(edge + 30 + step, H - edge - 30, step):
        for x in (edge + 30, W - edge - 30):
            c = MARIGOLD if k % 2 else MAROON
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=c)
            k += 1
    label = 140 if (title or detail) else 0
    print_ = contain(photo.image, (W - 2 * edge - 120, H - 2 * edge - 120 - label))
    px = (W - print_.width) // 2
    py = (H - label - print_.height) // 2 + 6
    shadow(canvas, (px, py, px + print_.width, py + print_.height), blur=14, offset=8, strength=60)
    canvas.paste(print_, (px, py))
    y = py + print_.height + 36
    if title:
        typeface, lines = fitted(draw, title, "serif", 44, W - 260, 1, weight="Medium")
        _centered(draw, y, lines[0], typeface, back.ink)
        y += line_height(typeface) + 6
    if detail:
        _centered(draw, y, detail, font("italic", 28), back.soft)
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


__all__ = ["FRAMES", "postcard", "ticket", "film", "magazine", "festive", "polaroids", "INK", "SOFT_INK"]
