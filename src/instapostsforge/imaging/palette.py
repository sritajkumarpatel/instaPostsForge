"""Colours taken from a photo, so a background can match it, and checks that keep text readable.

All of this is plain code: the palette is the photo's own colours, reduced, never an AI's choice.
"""

from __future__ import annotations

import colorsys

from PIL import Image

RGB = tuple[int, int, int]
INK: RGB = (48, 43, 38)
WHITE: RGB = (255, 255, 255)


def palette(image: Image.Image, n: int = 5) -> list[RGB]:
    """The photo's n main colours, most common first. Near-identical colours are merged."""
    small = image.convert("RGB").copy()
    small.thumbnail((160, 160))
    quantized = small.quantize(colors=max(n * 2, 8), method=Image.Quantize.MEDIANCUT)
    raw = quantized.getpalette() or []
    counts = sorted(quantized.getcolors() or [], reverse=True)
    colours: list[RGB] = []
    for _, index in counts:
        colour = tuple(raw[index * 3 : index * 3 + 3])
        if len(colour) == 3 and all(distance(colour, c) > 38 for c in colours):
            colours.append(colour)  # type: ignore[arg-type]
        if len(colours) == n:
            break
    return colours or [(128, 128, 128)]


def distance(a: RGB, b: RGB) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def mix(a: RGB, b: RGB, t: float) -> RGB:
    """a moved towards b by t (0 keeps a, 1 gives b)."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def luminance(c: RGB) -> float:
    def channel(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    """The WCAG contrast ratio, 1 to 21. Body text needs at least 4.5."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ink_for(background: RGB, preferred: RGB | None = None, minimum: float = 4.5) -> RGB:
    """A text colour readable on background: preferred if it is, else dark ink or white, whichever reads better."""
    if preferred is not None and contrast(preferred, background) >= minimum:
        return preferred
    return INK if contrast(INK, background) >= contrast(WHITE, background) else WHITE


def hsv(c: RGB) -> tuple[float, float, float]:
    return colorsys.rgb_to_hsv(*(v / 255 for v in c))


def soft_tint(c: RGB, lightness: float = 0.86) -> RGB:
    """A pale, quiet version of a colour, good as paper behind a print."""
    h, s, v = hsv(c)
    r, g, b = colorsys.hsv_to_rgb(h, min(s, 0.22), max(v, lightness))
    return mix((round(r * 255), round(g * 255), round(b * 255)), (238, 232, 222), 0.35)


def deep_tone(c: RGB) -> RGB:
    """A dark, muted version of a colour, for a dark background."""
    h, s, _ = hsv(c)
    r, g, b = colorsys.hsv_to_rgb(h, min(s, 0.45), 0.22)
    return (round(r * 255), round(g * 255), round(b * 255))


def accent(colours: list[RGB], background: RGB) -> RGB:
    """The most colourful palette colour that reads on background, for small type and marks."""
    ranked = sorted(colours, key=lambda c: hsv(c)[1] * hsv(c)[2], reverse=True)
    for c in ranked:
        if contrast(c, background) >= 3.0:
            return c
    return ink_for(background)


def warmest(colours: list[RGB]) -> RGB:
    """The palette colour closest to sunlight orange, for a sun shape."""
    return min(colours, key=lambda c: distance(c, (240, 150, 60)))


# --- flag and party colours ------------------------------------------------------------

def _kind(c: RGB) -> str:
    h, s, v = hsv(c)
    if v > 0.85 and s < 0.15:
        return "white"
    if s < 0.35 or v < 0.35:
        return "other"
    deg = h * 360
    if 15 <= deg < 45:
        return "saffron"
    if 90 <= deg < 160:
        return "green"
    if deg < 15 or deg >= 340:
        return "red"
    if 200 <= deg < 250:
        return "blue"
    return "other"


# Colour sets that read as a national flag or a party when shown as bands or swatches side by side.
FLAG_SETS = ({"saffron", "white", "green"}, {"saffron", "green"}, {"red", "white", "blue"})


def looks_like_flag(colours: list[RGB]) -> bool:
    """Whether these colours, shown together as bands or swatches, could read as a flag or a party's colours."""
    kinds = {_kind(c) for c in colours}
    return any(flag <= kinds for flag in FLAG_SETS)


def safe_swatches(colours: list[RGB]) -> list[RGB]:
    """The palette with colours dropped until it can't read as a flag; may come back shorter."""
    kept = list(colours)
    while kept and looks_like_flag(kept):
        # drop the least common colour that takes part in a flag set
        for c in reversed(kept):
            if _kind(c) in {"saffron", "green", "red", "blue", "white"}:
                kept.remove(c)
                break
    return kept
