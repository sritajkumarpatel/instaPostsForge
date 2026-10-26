"""Turns an original photo into a JPEG Instagram will accept.

The photo keeps its own aspect ratio whenever Instagram allows it. A photo just
outside the feed range (4:5 portrait to 1.91:1 landscape), such as a 3:4 phone
portrait, loses a thin centred strip to fit (at most max_crop of its length).
Only a photo further outside gets a border instead.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime

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
    taken_at: datetime | None = None  # from the photo's EXIF, when it has one
    shape: float = 0.0  # the photo's own width:height, before any border; equal shapes can share a multi-photo post
    cropped: bool = False  # a thin strip was trimmed to reach Instagram's range


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


def crop_to_ratio(image: Image.Image, min_ratio: float, max_ratio: float, max_crop: float) -> Image.Image | None:
    """Trims a centred strip so the ratio fits the range, or None if that would cut more than
    max_crop of the photo's height (too tall) or width (too wide)."""
    w, h = image.size
    if w / h < min_ratio:
        new_h = int(w / min_ratio)
        if 1 - new_h / h > max_crop:
            return None
        top = (h - new_h) // 2
        return image.crop((0, top, w, top + new_h))
    new_w = int(h * max_ratio)
    if 1 - new_w / w > max_crop:
        return None
    left = (w - new_w) // 2
    return image.crop((left, 0, left + new_w, h))


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
    taken_at = exif_time(image)
    image = ImageOps.exif_transpose(image).convert("RGB")
    shape = round(image.width / image.height, 3)

    padded = cropped = False
    min_ratio, max_ratio = float(settings.get("min_ratio", 0.8)), float(settings.get("max_ratio", 1.91))
    if settings.get("keep_aspect_ratio", True) and not min_ratio <= image.width / image.height <= max_ratio:
        trimmed = crop_to_ratio(image, min_ratio, max_ratio, float(settings.get("max_crop", 0.1)))
        if trimmed is not None:
            image, cropped = trimmed, True
    if settings.get("keep_aspect_ratio", True):
        image, padded = fit_ratio(
            image,
            min_ratio,
            max_ratio,
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
        taken_at=taken_at,
        shape=shape,
        cropped=cropped,
    )


_EXIF_IFD = 0x8769
_DATE_TIME_ORIGINAL = 36867
_DATE_TIME = 306
# Phone file names such as IMG20261018104837.jpg, IMG_20261018_104837.jpg or PXL_20261018_104837123.jpg
_NAME_TIME = re.compile(r"(20\d{2})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})")


def exif_time(image: Image.Image) -> datetime | None:
    """When the photo was taken, from its EXIF data."""
    try:
        exif = image.getexif()
        raw = exif.get_ifd(_EXIF_IFD).get(_DATE_TIME_ORIGINAL) or exif.get(_DATE_TIME)
        return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S") if raw else None
    except (ValueError, TypeError, AttributeError):
        return None


def name_time(file_name: str) -> datetime | None:
    """When the photo was taken, from a phone-style file name."""
    match = _NAME_TIME.search(file_name)
    if not match:
        return None
    try:
        return datetime(*(int(g) for g in match.groups()))
    except ValueError:
        return None


def fingerprint(jpeg: bytes) -> str:
    """A 64-bit difference hash: near-identical photos (resized, re-saved, lightly edited) get close values."""
    image = Image.open(io.BytesIO(jpeg)).convert("L").resize((9, 8), Image.LANCZOS)
    pixels = image.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return f"{bits:016x}"


def fingerprint_distance(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")
