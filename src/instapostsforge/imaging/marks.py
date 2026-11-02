"""A hand-drawn layer on top of a photo: notes with arrows, doodles, a constellation, a film date stamp,
viewfinder brackets, a highlighter circle, rule-of-thirds lines, a tape label, a magazine cover, or one
quiet sketch mark (a ring or rays around the subject, a few gulls, a handwritten aside).

The photo underneath is never edited: everything here is drawn on a separate transparent layer and
laid over a copy; the post is that one image. Code decides where every mark goes. The
model only names up to three things in the photo and roughly where they are (the analyst's anchors),
and writes the few words of each note (the caption writer); a note that can't be placed clear of the
subject is dropped, never squeezed in.

Rules kept here, whatever the inputs: no mark inside the subject band, at most three notes, marks
cover about a tenth of the frame at most, and nothing is ever drawn on a person (the pipeline drops
person anchors and never chooses doodles for photos with people).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageStat

from . import palette as pal
from .layouts import draw_spaced, fitted, font, line_height, text_width
from .processor import Focus

MARKS = (
    "field_notes", "doodles", "constellation", "date_stamp", "viewfinder", "highlight", "thirds", "tape", "cover",
    "orbit", "rays", "birds", "aside",
)
# Marks that only sit in a corner. On their own a notes post barely differs from the photo, so
# `draw_notes` adds a quiet sketch mark (or corner brackets) when only these fit.
ACCENT_MARKS = frozenset({"date_stamp"})
SKETCH_FALLBACKS = ("orbit", "rays", "viewfinder")
MAX_NOTES = 3
MAX_COVERAGE = 0.10  # share of the frame that notes and doodles may cover
WHITE = (255, 255, 255, 255)
STAMP = (255, 138, 36)


@dataclass
class Anchor:
    """A thing in the photo a note can point at: centre x and y as fractions of the width and height."""

    name: str
    x: float
    y: float
    note: str | None = None


@dataclass
class Drawn:
    image: Image.Image
    marks: list[str] = field(default_factory=list)  # the kinds actually drawn
    notes: list[str] = field(default_factory=list)  # the words actually written, for the alt text


class Layer:
    """The transparent layer the marks are drawn on, with a soft shadow so white reads on light areas."""

    def __init__(self, size: tuple[int, int], seed: int):
        self.size = size
        self.rng = random.Random(seed)
        self.ink = Image.new("RGBA", size, (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.ink)
        self.taken: list[tuple[int, int, int, int]] = []

    def wobbly(self, points: list[tuple[float, float]], width: int = 4, fill=WHITE, jitter: float = 1.6) -> None:
        """A line through points that wavers a little, as a hand-drawn line does."""
        out: list[tuple[float, float]] = []
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            steps = max(2, int(math.dist((x0, y0), (x1, y1)) / 14))
            for i in range(steps):
                t = i / steps
                out.append((x0 + (x1 - x0) * t + self.rng.uniform(-jitter, jitter), y0 + (y1 - y0) * t + self.rng.uniform(-jitter, jitter)))
        out.append(points[-1])
        self.draw.line(out, fill=fill, width=width, joint="curve")

    def text(self, xy: tuple[int, int], text: str, typeface, fill=WHITE) -> None:
        self.draw.text(xy, text, font=typeface, fill=fill)

    def flatten(self, photo: Image.Image) -> Image.Image:
        """The photo with the layer on top. The photo itself is a copy, never changed in place."""
        alpha = self.ink.getchannel("A")
        shade = Image.new("RGBA", self.size, (0, 0, 0, 0))
        shade.putalpha(alpha.filter(ImageFilter.GaussianBlur(4)).point(lambda a: a * 55 // 100))
        out = photo.convert("RGBA").copy()
        out.alpha_composite(shade, (2, 3))
        out.alpha_composite(self.ink)
        return out.convert("RGB")


# --- where marks may go -----------------------------------------------------------------


def subject_box(size: tuple[int, int], focus: Focus | None, pad: int = 16) -> tuple[int, int, int, int] | None:
    if focus is None:
        return None
    w, h = size
    return (int(focus.left * w) - pad, int(focus.top * h) - pad, int(focus.right * w) + pad, int(focus.bottom * h) + pad)


def overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int] | None) -> bool:
    return b is not None and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def inside(point: tuple[float, float], box: tuple[int, int, int, int] | None) -> bool:
    return box is not None and box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def crosses(a: tuple[float, float], b: tuple[float, float], box: tuple[int, int, int, int] | None) -> bool:
    """Whether the straight line from a to b passes through box."""
    return box is not None and any(inside((a[0] + (b[0] - a[0]) * t / 24, a[1] + (b[1] - a[1]) * t / 24), box) for t in range(25))


def clip_to(start: tuple[float, float], end: tuple[float, float], box: tuple[int, int, int, int] | None) -> tuple[float, float]:
    """end, or the last point before the line from start enters box, so an arrow stops at the subject's edge."""
    if box is None or not crosses(start, end, box):
        return end
    last = start
    for t in range(1, 61):
        p = (start[0] + (end[0] - start[0]) * t / 60, start[1] + (end[1] - start[1]) * t / 60)
        if inside(p, box):
            return last
        last = p
    return end


def busyness(photo: Image.Image, box: tuple[int, int, int, int]) -> float:
    """How much detail a region holds (0 is flat sky or wall). Notes go where it is lowest."""
    region = photo.crop(box).convert("L").filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(region).mean[0]


def free_spot(layer: Layer, photo: Image.Image, size: tuple[int, int], near: tuple[int, int] | None, keep_out) -> tuple[int, int] | None:
    """The top-left corner of the calmest place for a box of size, near a point if given, clear of the
    subject, of other marks and of the edges. None if there is nowhere."""
    w, h = photo.size
    bw, bh = size
    best, best_score = None, None
    margin = 28
    for gy in range(margin, h - bh - margin + 1, max(24, h // 24)):
        for gx in range(margin, w - bw - margin + 1, max(24, w // 18)):
            box = (gx, gy, gx + bw, gy + bh)
            if overlaps(box, keep_out) or any(overlaps(box, t) for t in layer.taken):
                continue
            score = busyness(photo, box) + layer.rng.uniform(0, 4)
            if layer.taken and near is None:  # spread loose marks out instead of lining them up
                closest = min(math.dist((gx + bw / 2, gy + bh / 2), ((t[0] + t[2]) / 2, (t[1] + t[3]) / 2)) for t in layer.taken)
                score -= min(closest, w / 2) / 25
            if near is not None:
                d = math.dist((gx + bw / 2, gy + bh / 2), near)
                if d > max(w, h) * 0.45:
                    continue
                score += d / 40
            if best_score is None or score < best_score:
                best, best_score = (gx, gy), score
    return best


def _coverage(layer: Layer) -> float:
    area = layer.size[0] * layer.size[1]
    return sum((b[2] - b[0]) * (b[3] - b[1]) for b in layer.taken) / area


# --- the marks ----------------------------------------------------------------------------


def arrow(layer: Layer, start: tuple[float, float], end: tuple[float, float], width: int = 4, keep_out=None) -> None:
    """A curved hand-drawn arrow from start that stops just short of end, or at the edge of keep_out
    when end is inside it. The bend is kept small enough not to swing into keep_out."""
    clipped = clip_to(start, end, keep_out)
    gap = 26 if clipped == end else 4
    end = clipped
    (x0, y0), (x1, y1) = start, end
    d = math.dist(start, end) or 1
    if d < 40:
        return
    x1, y1 = x1 - (x1 - x0) * gap / d, y1 - (y1 - y0) * gap / d
    bend = layer.rng.choice((-1, 1)) * d * (0.18 if keep_out is None else 0.06)
    mx, my = (x0 + x1) / 2 - (y1 - y0) / d * bend, (y0 + y1) / 2 + (x1 - x0) / d * bend
    curve = [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * mx + t * t * x1, (1 - t) ** 2 * y0 + 2 * (1 - t) * t * my + t * t * y1) for t in (i / 12 for i in range(13))]
    layer.wobbly(curve, width, jitter=0.8)
    ex, ey = curve[-1]
    px, py = curve[-3]
    angle = math.atan2(ey - py, ex - px)
    for side in (-0.5, 0.5):
        a = angle + math.pi + side
        layer.wobbly([(ex, ey), (ex + 22 * math.cos(a), ey + 22 * math.sin(a))], width, jitter=0.4)


def note_box(text: str, width: int) -> tuple[object, list[str], tuple[int, int]]:
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    hand, lines = fitted(draw, text, "hand", max(40, width // 18), width // 3, 2)
    w = max(text_width(draw, ln, hand) for ln in lines) + 16
    return hand, lines, (w, (line_height(hand) - 6) * len(lines) + 12)


def field_notes(layer: Layer, photo: Image.Image, anchors: list[Anchor], keep_out, circle_first: bool = False) -> list[str]:
    """Each anchor's note written in a calm spot near it, with an arrow to the thing it is about."""
    w, h = photo.size
    written: list[str] = []
    for i, anchor in enumerate(a for a in anchors if a.note):
        if len(written) >= MAX_NOTES or _coverage(layer) > MAX_COVERAGE:
            break
        target = (anchor.x * w, anchor.y * h)
        hand, lines, size = note_box(anchor.note, w)
        spot = free_spot(layer, photo, size, target, keep_out)
        if spot is None:
            continue
        box = (spot[0], spot[1], spot[0] + size[0], spot[1] + size[1])
        layer.taken.append(box)
        y = spot[1]
        for ln in lines:
            layer.text((spot[0] + 8, y), ln, hand)
            y += line_height(hand) - 6
        r = max(60, w // 12)
        ring = (int(target[0] - r * 1.2), int(target[1] - r), int(target[0] + r * 1.2), int(target[1] + r))
        if circle_first and i == 0 and not overlaps(ring, keep_out) and not any(overlaps(ring, t) for t in layer.taken[:-1]):
            loop = [(target[0] + r * math.cos(t) * 1.1, target[1] + r * math.sin(t) * 0.85) for t in (k / 18 * 2 * math.pi * 1.08 for k in range(20))]
            layer.wobbly(loop, 5, jitter=2.0)
        cx = min(max(target[0], box[0]), box[2])
        cy = box[3] + 6 if target[1] > box[3] else box[1] - 6 if target[1] < box[1] else (box[1] + box[3]) / 2
        if not (box[0] <= target[0] <= box[2] and box[1] <= target[1] <= box[3]):
            if math.dist((cx, cy), target) > 60:
                arrow(layer, (cx, cy), target, keep_out=keep_out)
        written.append(anchor.note)
    return written


def _sparkle(layer: Layer, x: float, y: float, r: float) -> None:
    layer.wobbly([(x - r, y), (x + r, y)], 3, jitter=0.5)
    layer.wobbly([(x, y - r), (x, y + r)], 3, jitter=0.5)
    s = r * 0.45
    layer.wobbly([(x - s, y - s), (x + s, y + s)], 2, jitter=0.3)
    layer.wobbly([(x - s, y + s), (x + s, y - s)], 2, jitter=0.3)


def _star(layer: Layer, x: float, y: float, r: float) -> None:
    pts = []
    for k in range(11):
        a = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.45
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    layer.wobbly(pts, 3, jitter=0.7)


def _swirl(layer: Layer, x: float, y: float, r: float) -> None:
    pts = [(x + (r * t / 14) * math.cos(t * 0.9), y + (r * t / 14) * math.sin(t * 0.9)) for t in range(15)]
    layer.wobbly(pts, 3, jitter=0.6)


def _cloud(layer: Layer, x: float, y: float, r: float) -> None:
    pts = []
    for bump, (cx, cy, br) in enumerate(((x - r * 0.6, y, r * 0.45), (x, y - r * 0.25, r * 0.6), (x + r * 0.6, y, r * 0.45))):
        start, end = (math.pi * 0.9, math.pi * 2.05) if bump != 1 else (math.pi * 1.05, math.pi * 1.95)
        pts += [(cx + br * math.cos(a), cy + br * math.sin(a)) for a in (start + (end - start) * i / 8 for i in range(9))]
    pts.append((x - r * 0.6 - r * 0.45, y + 2))
    layer.wobbly(pts, 3, jitter=0.6)


def _steam(layer: Layer, x: float, y: float, r: float) -> None:
    for dx in (-r * 0.5, 0, r * 0.5):
        pts = [(x + dx + math.sin(t / 2) * r * 0.18, y - t * r / 6) for t in range(7)]
        layer.wobbly(pts, 3, jitter=0.4)


DOODLES = {"sparkle": _sparkle, "star": _star, "swirl": _swirl, "cloud": _cloud, "steam": _steam}


def doodles(layer: Layer, photo: Image.Image, keep_out, kinds: tuple[str, ...], steam_at: tuple[float, float] | None = None) -> int:
    """A handful of small sketch marks in the plain parts of the frame, away from the subject."""
    w, _ = photo.size
    drawn = 0
    if steam_at is not None and "steam" in kinds:  # steam rises from the dish, so it starts just above it
        r = w / 16
        box = (int(steam_at[0] - r), int(steam_at[1] - r * 1.4), int(steam_at[0] + r), int(steam_at[1] - r * 0.2))
        if not overlaps(box, keep_out) and not any(overlaps(box, t) for t in layer.taken):
            _steam(layer, steam_at[0], steam_at[1] - r * 0.25, r)
            layer.taken.append(box)
            drawn += 1
    others = [k for k in kinds if k != "steam"] or ["sparkle"]
    for _ in range(layer.rng.randint(2, 3)):  # a few small accents, never a sticker sheet
        if _coverage(layer) > MAX_COVERAGE:
            break
        r = layer.rng.uniform(w / 40, w / 22)
        size = (int(r * 2.4), int(r * 2.4))
        spot = free_spot(layer, photo, size, None, keep_out)
        if spot is None:
            break
        layer.taken.append((spot[0], spot[1], spot[0] + size[0], spot[1] + size[1]))
        DOODLES[layer.rng.choice(others)](layer, spot[0] + size[0] / 2, spot[1] + size[1] / 2, r)
        drawn += 1
    return drawn


def bright_points(photo: Image.Image, limit: int = 8) -> list[tuple[int, int]]:
    """Bright, separate points in a dark area: stars, lamps or planets in a night sky."""
    scale = 4
    gray = photo.convert("L")
    # a star may be one pixel wide: keep the brightest pixel of each block, not the average
    small = gray.filter(ImageFilter.MaxFilter(5)).resize((photo.width // scale, photo.height // scale), Image.NEAREST)
    surround_src = gray.resize((photo.width // scale, photo.height // scale), Image.BOX)
    peaks = small.filter(ImageFilter.MaxFilter(5))
    surround = surround_src.filter(ImageFilter.BoxBlur(10))
    sw, sh = small.size
    px, pk, sr = small.load(), peaks.load(), surround.load()
    found: list[tuple[int, int, int]] = []
    for y in range(2, sh - 2):
        for x in range(2, sw - 2):
            v = px[x, y]
            if v >= 110 and v == pk[x, y] and sr[x, y] < 70 and v - sr[x, y] > 45:
                found.append((v, x * scale, y * scale))
    found.sort(reverse=True)
    points: list[tuple[int, int]] = []
    for _, x, y in found:
        if all(math.dist((x, y), p) > photo.width / 16 for p in points):
            points.append((x, y))
        if len(points) == limit:
            break
    return points


def constellation(layer: Layer, photo: Image.Image, label: str | None, keep_out) -> list[str] | None:
    """Joins four to seven bright points in the sky with thin lines, like a star map, and writes a
    small label by it. None when the sky doesn't have enough clear points."""
    found = [p for p in bright_points(photo, 30) if not overlaps((p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4), keep_out)]
    if not found:
        return None
    # a small, tight figure around the brightest point, not lines across the whole sky
    reach = photo.width * 0.3
    centre = max(found, key=lambda c: sum(math.dist(c, p) <= reach for p in found))  # where the points cluster
    points = sorted((p for p in found if math.dist(p, centre) <= reach), key=lambda p: math.dist(p, centre))[:7]
    if len(points) < 4:
        return None
    chain = [points.pop(0)]
    while points:
        nxt = min(points, key=lambda p: math.dist(p, chain[-1]))
        points.remove(nxt)
        if not crosses(chain[-1], nxt, keep_out):  # a line through the subject is left out
            chain.append(nxt)
    if len(chain) < 4:
        return None
    soft = (255, 246, 210, 220)
    for a, b in zip(chain, chain[1:]):
        layer.wobbly([a, b], 2, fill=soft, jitter=0.4)
    for x, y in chain:
        layer.draw.ellipse((x - 7, y - 7, x + 7, y + 7), outline=soft, width=2)
    xs, ys = [p[0] for p in chain], [p[1] for p in chain]
    layer.taken.append((min(xs) - 10, min(ys) - 10, max(xs) + 10, max(ys) + 10))
    written: list[str] = []
    if label:
        hand, lines, size = note_box(label, photo.width)
        spot = free_spot(layer, photo, size, (sum(xs) / len(xs), max(ys) + 60), keep_out)
        if spot:
            y = spot[1]
            for ln in lines:
                layer.text((spot[0] + 8, y), ln, hand, fill=soft)
                y += line_height(hand) - 6
            layer.taken.append((spot[0], spot[1], spot[0] + size[0], spot[1] + size[1]))
            written.append(label)
    return written


# --- sketch marks: one quiet line that plays off the subject without touching it --------
#
# Taken from how photographers and illustrators doodle on photos (a ring around the moon, a few rays
# off a lamp, two gulls in an empty sky, one handwritten word): one colour, a thin line, drawn in the
# empty space around the subject so the photo still reads first.


def _ring_points(cx: float, cy: float, rx: float, ry: float, tilt: float, start: float, sweep: float, n: int = 48):
    pts = []
    for k in range(n + 1):
        t = start + sweep * k / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((cx + x * math.cos(tilt) - y * math.sin(tilt), cy + x * math.sin(tilt) + y * math.cos(tilt)))
    return pts


def _runs_outside(points: list[tuple[float, float]], box) -> list[list[tuple[float, float]]]:
    """The points split into runs that stay out of box, so a line can skip the subject."""
    runs: list[list[tuple[float, float]]] = [[]]
    for p in points:
        if inside(p, box):
            if runs[-1]:
                runs.append([])
        else:
            runs[-1].append(p)
    return [r for r in runs if len(r) > 1]


def orbit(layer: Layer, photo: Image.Image, subject) -> bool:
    """A thin, slightly tilted hand-drawn ring around the subject, left open like a pen loop, with a
    small dot riding on it. Only around a subject with room to spare; never around a person."""
    if subject is None:
        return False
    w, h = photo.size
    x0, y0, x1, y1 = subject
    bw, bh = x1 - x0, y1 - y0
    if bw > w * 0.6 or bh > h * 0.6:
        return False
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = bw / 2 * 1.7 + 18, bh / 2 * 1.45 + 18  # wide enough that the loop clears the subject's corners
    if bw < bh * 0.8:  # a tall subject gets an upright ring
        rx, ry = bw / 2 * 1.45 + 18, bh / 2 * 1.7 + 18
    tilt = layer.rng.uniform(-0.28, -0.12)
    start = layer.rng.uniform(0, 2 * math.pi)
    pts = _ring_points(cx, cy, rx, ry, tilt, start, 2 * math.pi * 0.86)
    if any(not (24 <= x <= w - 24 and 24 <= y <= h - 24) for x, y in pts):
        return False
    runs = _runs_outside(pts, subject)
    if sum(len(r) for r in runs) < len(pts) * 0.8:
        return False
    for run in runs:
        layer.wobbly(run, 3, fill=(255, 255, 255, 235), jitter=0.9)
    dot = _ring_points(cx, cy, rx, ry, tilt, start + 2 * math.pi * 0.93, 0, 1)[0]
    if not inside(dot, subject):
        layer.draw.ellipse((dot[0] - 7, dot[1] - 7, dot[0] + 7, dot[1] + 7), fill=WHITE)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    layer.taken.append((int(min(xs)) - 8, int(min(ys)) - 8, int(max(xs)) + 8, int(max(ys)) + 8))
    return True


def rays(layer: Layer, photo: Image.Image, subject) -> bool:
    """Short hand-drawn strokes radiating from just outside the subject, like a pen sketch of its glow.
    Never around a person; at least four strokes must fit in the frame."""
    if subject is None:
        return False
    w, h = photo.size
    x0, y0, x1, y1 = subject
    if x1 - x0 > w * 0.55 or y1 - y0 > h * 0.55:
        return False
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    reach = math.dist((x0, y0), (cx, cy)) + 12  # beyond the box's corners, so no stroke starts inside it
    count = 8
    turn = layer.rng.uniform(0, math.pi / count)
    strokes = []
    for k in range(count):
        a = turn + 2 * math.pi * k / count
        length = max(28, min(w // 14, reach * (0.3 if k % 2 == 0 else 0.18)))
        s = (cx + reach * math.cos(a), cy + reach * math.sin(a))
        e = (cx + (reach + length) * math.cos(a), cy + (reach + length) * math.sin(a))
        if all(24 <= v <= lim - 24 for v, lim in ((s[0], w), (e[0], w), (s[1], h), (e[1], h))) and not crosses(s, e, subject):
            strokes.append((s, e))
    if len(strokes) < 4:
        return False
    for s, e in strokes:
        layer.wobbly([s, e], 3, fill=(255, 255, 255, 235), jitter=0.5)
    pts = [p for st in strokes for p in st]
    layer.taken.append((int(min(p[0] for p in pts)) - 6, int(min(p[1] for p in pts)) - 6, int(max(p[0] for p in pts)) + 6, int(max(p[1] for p in pts)) + 6))
    return True


def _gull(layer: Layer, x: float, y: float, r: float) -> None:
    """A gull as a child or a sketchbook draws one: two soft wing arcs meeting in a dip."""
    for side in (-1, 1):
        wing = [(x + side * r * t, y - math.sin(math.pi * (0.15 + 0.7 * t)) * r * 0.42 + t * r * 0.1) for t in (i / 10 for i in range(11))]
        layer.wobbly(wing, 3, jitter=0.3)


def birds(layer: Layer, photo: Image.Image, keep_out) -> bool:
    """Two or three small gulls, a few pen strokes each, in the calmest part of the upper frame."""
    w, h = photo.size
    size = (int(w * 0.3), int(w * 0.13))
    spot = free_spot(layer, photo, size, (w * layer.rng.choice((0.28, 0.72)), h * 0.18), keep_out)
    if spot is None or spot[1] + size[1] > h * 0.55 or busyness(photo, (spot[0], spot[1], spot[0] + size[0], spot[1] + size[1])) > 12:
        return False
    bx, by = spot
    flock = [(0.22, 0.62, 1.0), (0.55, 0.3, 0.7), (0.8, 0.7, 0.55)][: layer.rng.choice((2, 3))]
    r = w / 22
    for fx, fy, scale in flock:
        _gull(layer, bx + size[0] * fx, by + size[1] * fy, r * scale)
    layer.taken.append((bx, by, bx + size[0], by + size[1]))
    return True


def aside(layer: Layer, photo: Image.Image, text: str | None, keep_out) -> list[str] | None:
    """One handwritten aside with a loose underline, in a calm spot near the subject, the way a
    photographer jots a word on a print. None without words or a place to write them."""
    if not text:
        return None
    w, h = photo.size
    hand, lines, size = note_box(text, w)
    size = (size[0], size[1] + 18)
    near = None
    if keep_out is not None:
        near = ((keep_out[0] + keep_out[2]) / 2, keep_out[3] + 70 if keep_out[3] < h * 0.7 else keep_out[1] - 70)
    spot = free_spot(layer, photo, size, near, keep_out)
    if spot is None:
        return None
    y = spot[1]
    for ln in lines:
        layer.text((spot[0] + 8, y), ln, hand)
        y += line_height(hand) - 6
    last = text_width(ImageDraw.Draw(Image.new("L", (1, 1))), lines[-1], hand)
    ux, uy = spot[0] + 6, y + 8
    swoosh = [(ux + last * t, uy + math.sin(t * math.pi) * 5 - t * 4) for t in (i / 10 for i in range(11))]
    layer.wobbly(swoosh, 3, jitter=0.6)
    layer.taken.append((spot[0], spot[1], spot[0] + size[0], spot[1] + size[1]))
    return [text]


# Seven-segment digits, as an old film camera printed the date: segments a to g.
SEGMENTS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
    "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg",
}


def _digit(draw: ImageDraw.ImageDraw, x: float, y: float, ch: str, size: float, fill) -> float:
    if ch == "'":
        draw.polygon([(x + size * 0.1, y), (x + size * 0.22, y), (x + size * 0.12, y + size * 0.28)], fill=fill)
        return size * 0.32
    if ch == " ":
        return size * 0.35
    w, t = size * 0.55, size * 0.11
    segs = {
        "a": [(x + t, y), (x + w - t, y), (x + w - 2 * t, y + t), (x + 2 * t, y + t)],
        "b": [(x + w, y + t), (x + w, y + size / 2 - t / 2), (x + w - t, y + size / 2 - t), (x + w - t, y + 2 * t)],
        "c": [(x + w, y + size / 2 + t / 2), (x + w, y + size - t), (x + w - t, y + size - 2 * t), (x + w - t, y + size / 2 + t)],
        "d": [(x + 2 * t, y + size - t), (x + w - 2 * t, y + size - t), (x + w - t, y + size), (x + t, y + size)],
        "e": [(x, y + size / 2 + t / 2), (x + t, y + size / 2 + t), (x + t, y + size - 2 * t), (x, y + size - t)],
        "f": [(x, y + t), (x + t, y + 2 * t), (x + t, y + size / 2 - t), (x, y + size / 2 - t / 2)],
        "g": [(x + t, y + size / 2 - t / 2), (x + w - t, y + size / 2 - t / 2), (x + w - t, y + size / 2 + t / 2), (x + t, y + size / 2 + t / 2)],
    }
    for s in SEGMENTS.get(ch, ""):
        draw.polygon(segs[s], fill=fill)
    return w + size * 0.18


def date_stamp(photo: Image.Image, when: datetime, keep_out) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """The orange date an old film camera printed in the corner, "'26 11 01", with its soft glow. Drawn
    on its own layer; returns the layer and the corner it sits in, or None if every corner holds the subject."""
    w, h = photo.size
    size = max(30, w // 26)
    text = when.strftime("'%y %m %d")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width = sum(_digit(ImageDraw.Draw(Image.new("L", (1, 1))), 0, 0, ch, size, 0) for ch in text)
    corners = [(w - width - 44, h - size - 44), (44, h - size - 44), (w - width - 44, 44)]
    spot = next(((cx, cy) for cx, cy in corners if not overlaps((int(cx) - 6, int(cy) - 6, int(cx + width) + 6, int(cy + size) + 6), keep_out)), None)
    if spot is None:
        return None
    x, y = spot
    cx = x
    for ch in text:
        cx += _digit(draw, cx, y, ch, size, STAMP + (235,))
    glow = layer.filter(ImageFilter.GaussianBlur(5))
    glow.alpha_composite(layer)
    return glow, (int(x), int(y), int(x + width), int(y + size))


def viewfinder(layer: Layer, photo: Image.Image, subject, label: str | None, keep_out=None) -> None:
    """Thin corner brackets just inside the frame, focus brackets around the subject (when it isn't a
    person; the pipeline passes None then), and a small line of time and light at the foot."""
    w, h = photo.size
    inset, arm = 36, max(50, w // 16)
    for cx, cy, dx, dy in ((inset, inset, 1, 1), (w - inset, inset, -1, 1), (inset, h - inset, 1, -1), (w - inset, h - inset, -1, -1)):
        corner = (min(cx, cx + dx * arm) - 3, min(cy, cy + dy * arm) - 3, max(cx, cx + dx * arm) + 3, max(cy, cy + dy * arm) + 3)
        if not overlaps(corner, keep_out):  # a corner that would sit on the subject is left off
            layer.draw.line([(cx, cy + dy * arm), (cx, cy), (cx + dx * arm, cy)], fill=WHITE, width=3)
    if subject is not None:
        x0, y0, x1, y1 = subject
        a = max(26, min(x1 - x0, y1 - y0) // 6)
        for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            layer.draw.line([(cx, cy + dy * a), (cx, cy), (cx + dx * a, cy)], fill=(255, 255, 255, 220), width=3)
    if label:
        small = font("serif", max(20, w // 46), "SemiBold")
        box = (inset + 20, h - inset - 44, inset + 20 + text_width(layer.draw, label.upper(), small, 3), h - inset - 44 + line_height(small))
        if not overlaps(box, keep_out):
            draw_spaced(layer.draw, box[:2], label.upper(), small, WHITE, 3)


def _around(a: tuple[float, float], b: tuple[float, float], box) -> list[tuple[float, float, float, float]]:
    """A straight horizontal or vertical line from a to b, in pieces that skip box."""
    if box is None or not crosses(a, b, box):
        return [(*a, *b)]
    if a[0] == b[0]:  # vertical
        return [s for s in ((a[0], a[1], a[0], box[1] - 1), (a[0], box[3] + 1, a[0], b[1])) if s[3] > s[1]]
    return [s for s in ((a[0], a[1], box[0] - 1, a[1]), (box[2] + 1, a[1], b[0], a[1])) if s[2] > s[0]]


def thirds(layer: Layer, photo: Image.Image, subject, note: str | None, keep_out) -> list[str]:
    """Light rule-of-thirds lines, a ring where the subject meets them, and a note on why the frame works."""
    w, h = photo.size
    faint = (255, 255, 255, 120)
    for k in (1, 2):
        x, y = w * k / 3, h * k / 3
        for seg in _around((x, 0), (x, h), keep_out):
            layer.draw.line(seg, fill=faint, width=2)
        for seg in _around((0, y), (w, y), keep_out):
            layer.draw.line(seg, fill=faint, width=2)
    if subject is not None:
        sx, sy = (subject[0] + subject[2]) / 2, (subject[1] + subject[3]) / 2
        ix, iy = min(((w * a / 3, h * b / 3) for a in (1, 2) for b in (1, 2)), key=lambda p: math.dist(p, (sx, sy)))
        if not overlaps((int(ix) - 18, int(iy) - 18, int(ix) + 18, int(iy) + 18), keep_out):
            layer.draw.ellipse((ix - 16, iy - 16, ix + 16, iy + 16), outline=WHITE, width=3)
    if not note:
        return []
    hand, lines, size = note_box(note, w)
    spot = free_spot(layer, photo, size, None, keep_out)
    if spot is None:
        return []
    y = spot[1]
    for ln in lines:
        layer.text((spot[0] + 8, y), ln, hand)
        y += line_height(hand) - 6
    layer.taken.append((spot[0], spot[1], spot[0] + size[0], spot[1] + size[1]))
    return [note]


def tape(photo: Image.Image, text: str, colours: list, keep_out, seed: int) -> Image.Image | None:
    """A strip of washi tape across a corner with the place name written on it. None if every corner
    would cover the subject."""
    w, h = photo.size
    rng = random.Random(seed)
    tw, th = max(300, w // 3), max(70, w // 14)
    strip = Image.new("RGBA", (tw, th), pal.soft_tint(colours[0] if colours else (230, 220, 200), 0.9) + (215,))
    sdraw = ImageDraw.Draw(strip)
    for x in range(0, tw, 16):  # torn ends
        sdraw.polygon([(x, 0), (x + 8, 5), (x + 16, 0)], fill=(0, 0, 0, 0))
    hand, lines = fitted(sdraw, text, "hand", th - 18, tw - 40, 1)
    tint = pal.ink_for(pal.soft_tint(colours[0] if colours else (230, 220, 200), 0.9))
    sdraw.text(((tw - text_width(sdraw, lines[0], hand)) // 2, 4), lines[0], font=hand, fill=tint + (255,))
    for corner, angle in (("tl", rng.uniform(-14, -8)), ("br", rng.uniform(-14, -8)), ("tr", rng.uniform(8, 14)), ("bl", rng.uniform(8, 14))):
        turned = strip.rotate(angle, resample=Image.BICUBIC, expand=True)
        x = 30 if corner in ("tl", "bl") else w - turned.width - 30
        y = 40 if corner in ("tl", "tr") else h - turned.height - 40
        if not overlaps((x, y, x + turned.width, y + turned.height), keep_out):
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            layer.alpha_composite(turned, (x, y))
            return layer
    return None


def cover(layer: Layer, photo: Image.Image, title: str, lines: list[str], issue: str, subject) -> list[str] | None:
    """The photo as a magazine cover: the title as a masthead at whichever end of the frame is clear of
    the subject, a small issue line, and up to two cover lines in the lower corner."""
    w, h = photo.size
    top_clear = subject is None or subject[1] > h * 0.22
    head, heads = fitted(layer.draw, title.upper(), "serif", max(80, w // 8), w - 120, 1, spacing=6, weight="SemiBold")
    hy = 50 if top_clear else h - 60 - line_height(head)
    if overlaps((40, hy, w - 40, hy + line_height(head) + 40), subject):  # the subject fills the frame: no room for a masthead
        return None
    draw_spaced(layer.draw, ((w - text_width(layer.draw, heads[0], head, 6)) // 2, hy), heads[0], head, WHITE, 6)
    small = font("serif", max(18, w // 54), "SemiBold")
    iy = hy + line_height(head) + 6 if top_clear else hy - 34
    if overlaps((40, iy, w - 40, iy + 30), subject):
        iy = -100  # no issue line rather than one over the subject
    draw_spaced(layer.draw, ((w - text_width(layer.draw, issue.upper(), small, 3)) // 2, iy), issue.upper(), small, WHITE, 3)
    written: list[str] = []
    body = font("serif", max(26, w // 34), "Medium")
    y = h - 70 - (line_height(body) + 10) * len(lines[:2]) if top_clear else 60 + line_height(head) + 40
    for text in lines[:2]:
        box = (60, y, 60 + text_width(layer.draw, text, body), y + line_height(body))
        if overlaps(box, subject):
            continue
        layer.draw.line((60, y + line_height(body) // 2, 80, y + line_height(body) // 2), fill=WHITE, width=3)
        layer.text((94, y), text, body)
        written.append(text)
        y += line_height(body) + 10
    return written


def draw(
    photo: Image.Image,
    marks: list[str],
    *,
    anchors: list[Anchor] | None = None,
    focus: Focus | None = None,
    subject_is_person: bool = False,
    taken_at: datetime | None = None,
    light_label: str | None = None,
    place: str | None = None,
    title: str | None = None,
    line: str | None = None,
    note: str | None = None,
    issue: str = "",
    colours: list | None = None,
    steam: bool = False,
    seed: int = 0,
) -> Drawn:
    """The photo with the chosen marks drawn over it. A mark that can't be placed is skipped; the
    result lists the ones drawn, which may be none."""
    layer = Layer(photo.size, seed)
    keep_out = subject_box(photo.size, focus)
    subject = None if subject_is_person else keep_out
    anchors = anchors or []
    drawn: list[str] = []
    words: list[str] = []
    extra_layers: list[Image.Image] = []
    for mark in marks:
        if mark == "field_notes" and (got := field_notes(layer, photo, anchors, keep_out)):
            drawn.append(mark)
            words += got
        elif mark == "highlight" and (got := field_notes(layer, photo, anchors[:1], keep_out, circle_first=True)):
            drawn.append(mark)
            words += got
        elif mark == "doodles":
            steam_at = (anchors[0].x * photo.width, anchors[0].y * photo.height) if anchors and steam else None
            kinds = ("steam", "sparkle", "swirl") if steam_at is not None else ("sparkle", "star", "swirl", "cloud")
            if doodles(layer, photo, keep_out, kinds, steam_at):
                drawn.append(mark)
        elif mark == "constellation" and (got := constellation(layer, photo, note, keep_out)) is not None:
            drawn.append(mark)
            words += got
        elif mark == "date_stamp" and taken_at is not None and (stamped := date_stamp(photo, taken_at, keep_out)) is not None:
            stamp, box = stamped
            extra_layers.append(stamp)
            layer.taken.append(box)
            drawn.append(mark)
        elif mark == "viewfinder":
            viewfinder(layer, photo, subject, light_label, keep_out)
            drawn.append(mark)
        elif mark == "thirds":
            words += thirds(layer, photo, subject, line, keep_out)
            drawn.append(mark)
        elif mark == "tape" and place and (strip := tape(photo, place, colours or [], keep_out, seed)) is not None:
            extra_layers.append(strip)
            drawn.append(mark)
        elif mark == "orbit" and orbit(layer, photo, subject):
            drawn.append(mark)
        elif mark == "rays" and rays(layer, photo, subject):
            drawn.append(mark)
        elif mark == "birds" and not subject_is_person and birds(layer, photo, keep_out):
            drawn.append(mark)
        elif mark == "aside" and (got := aside(layer, photo, note, keep_out)) is not None:
            drawn.append(mark)
            words += got
        elif mark == "cover" and title and (got := cover(layer, photo, title, [a.note for a in anchors if a.note], issue, keep_out)) is not None:
            words += got
            drawn.append(mark)
    out = layer.flatten(photo)
    for extra in extra_layers:  # the stamp and tape carry their own colour, so they skip the shadow
        base = out.convert("RGBA")
        base.alpha_composite(extra)
        out = base.convert("RGB")
    return Drawn(out, drawn, words)


def draw_notes(photo: Image.Image, marks: list[str], **options) -> Drawn:
    """The marks for a notes post. When only a corner accent (the date stamp) or nothing
    could be placed, a quiet sketch mark is added (a ring or rays around the subject, else corner
    brackets), so the post is never just the photo with a date in the corner."""
    drawn = draw(photo, marks, **options)
    if set(drawn.marks) - ACCENT_MARKS:
        return drawn
    accents = [m for m in drawn.marks if m in ACCENT_MARKS]
    for extra in SKETCH_FALLBACKS:
        if extra in marks:
            continue
        again = draw(photo, [extra, *accents], **options)
        if extra in again.marks:
            return again
    return draw(photo, ["viewfinder", *accents], **options)
