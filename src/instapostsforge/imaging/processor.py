"""Turns an original photo into a JPEG Instagram will accept.

The photo keeps its own aspect ratio whenever Instagram allows it. Only a photo
outside the feed range (4:5 portrait to 1.91:1 landscape) gets a border, and
nothing is ever cropped.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, ImageStat

try:  # iPhone photos arrive as HEIC; support them when pillow-heif is installed.
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass


@dataclass
class PreparedImage:
    jpeg: bytes
    width: int
    height: int
    padded: bool
    preview_jpeg: bytes  # a smaller copy for the caption writer


def _pad_color(image: Image.Image, setting: str) -> tuple[int, int, int]:
    if setting and setting != "auto":
        return hex_to_rgb(setting)
    # The average colour of the photo's border blends in better than white.
    w, h = image.size
    edge = max(1, min(w, h) // 50)
    strips = [
        image.crop((0, 0, w, edge)),
        image.crop((0, h - edge, w, h)),
        image.crop((0, 0, edge, h)),
        image.crop((w - edge, 0, w, h)),
    ]
    means = [ImageStat.Stat(s).mean for s in strips]
    return tuple(int(sum(m[i] for m in means) / len(means)) for i in range(3))  # type: ignore[return-value]


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"pad_color must be 'auto' or a hex colour like #ffffff, got {hex_color!r}")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def fit_ratio(image: Image.Image, min_ratio: float, max_ratio: float, pad_color: str) -> tuple[Image.Image, bool]:
    """Pads the image onto a canvas only when its ratio is outside the allowed range."""
    w, h = image.size
    ratio = w / h
    if min_ratio <= ratio <= max_ratio:
        return image, False
    color = _pad_color(image, pad_color)
    if ratio < min_ratio:  # too tall: widen the canvas
        canvas = Image.new("RGB", (round(h * min_ratio), h), color)
    else:  # too wide: heighten the canvas
        canvas = Image.new("RGB", (w, round(w / max_ratio)), color)
    canvas.paste(image, ((canvas.width - w) // 2, (canvas.height - h) // 2))
    return canvas, True


def _to_jpeg(image: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()


def prepare_image(original: bytes, settings: dict) -> PreparedImage:
    image = Image.open(io.BytesIO(original))
    image = ImageOps.exif_transpose(image).convert("RGB")

    padded = False
    if settings.get("keep_aspect_ratio", True):
        image, padded = fit_ratio(
            image,
            float(settings.get("min_ratio", 0.8)),
            float(settings.get("max_ratio", 1.91)),
            str(settings.get("pad_color", "auto")),
        )

    max_width = int(settings.get("max_width", 1440))
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)), Image.LANCZOS)

    preview = image.copy()
    preview.thumbnail((1024, 1024), Image.LANCZOS)

    return PreparedImage(
        jpeg=_to_jpeg(image, int(settings.get("jpeg_quality", 90))),
        width=image.width,
        height=image.height,
        padded=padded,
        preview_jpeg=_to_jpeg(preview, 85),
    )
