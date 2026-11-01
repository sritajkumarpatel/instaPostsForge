"""The main subject of a photo as a mask, for the pop-out poster, and the checks that decide if it's usable.

A segmentation model only answers "which pixels belong to the subject"; it returns a grey mask (white is
subject) and never changes or invents a pixel. The poster then shows the photo's own pixels through that
mask. The model runs locally with onnxruntime (`pip install -e ".[cutout]"`); its file is downloaded once
from a pinned URL and checked against a SHA-256 before it is used. Without the extra, or when the download
or check fails, `CutoutUnavailable` is raised and the post falls back to a plain poster.

The checks (`check_mask`) are plain code with thresholds from `selection.style.popout`. Every failed check
names its reason, so a skipped pop-out is easy to explain in the run summary.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from .processor import Focus

log = logging.getLogger(__name__)

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
_RELEASES = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"


@dataclass(frozen=True)
class Model:
    file: str
    url: str
    sha256: str
    size: int  # the square the model reads
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    sigmoid: bool  # whether the raw output still needs squashing to 0..1
    licence: str


# BiRefNet-lite gives the cleanest edges (about 10 to 25 s on a CPU); IS-Net is about 1 s and is the one
# Postaday runs on the phone. Both are converted to ONNX and published by the rembg project.
MODELS: dict[str, Model] = {
    "birefnet-lite": Model(
        file="birefnet-general-lite.onnx",
        url=_RELEASES + "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx",
        sha256="5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333",
        size=1024,
        mean=IMAGENET_MEAN,
        std=IMAGENET_STD,
        sigmoid=True,
        licence="MIT",
    ),
    "isnet": Model(
        file="isnet-general-use.onnx",
        url=_RELEASES + "isnet-general-use.onnx",
        sha256="60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a",
        size=1024,
        mean=(0.5, 0.5, 0.5),
        std=(1.0, 1.0, 1.0),
        sigmoid=False,
        licence="Apache-2.0",
    ),
}
DEFAULT_MODEL = "birefnet-lite"
DEFAULT_CACHE = Path.home() / ".cache" / "instapostsforge" / "models"

# What check_mask allows, unless selection.style.popout says otherwise.
DEFAULTS = {
    "model": DEFAULT_MODEL,
    "min_area": 0.03,  # the subject covers at least this share of the photo...
    "max_area": 0.60,  # ...and at most this
    "max_second_piece": 0.15,  # a second blob bigger than this share of the main one means the subject is in pieces
    "min_box_overlap": 0.5,  # at least this share of the mask lies in the analyst's subject box
    "max_soft_edge": 0.12,  # at most this share of the mask is half-transparent (fog, motion blur, glass)
    "min_ground": 0.06,  # stepping down needs this much of the photo's height under the subject
    "min_margin": 0.02,  # a side the subject steps out of must have this much room in the photo
}

# Each way the subject can leave the window, and the side of the photo that needs room for it.
VARIANTS = ("step_down", "step_left", "step_right", "break_frame")


class CutoutUnavailable(Exception):
    """The model can't run here: the extra isn't installed, or its file can't be fetched or trusted."""


def settings_from(selection: dict | None) -> dict:
    """`selection.style.popout` over the defaults, with the model name checked."""
    chosen = dict(DEFAULTS)
    chosen.update(((selection or {}).get("style") or {}).get("popout") or {})
    if os.environ.get("POPOUT_MODEL"):
        chosen["model"] = os.environ["POPOUT_MODEL"]
    if chosen["model"] not in MODELS:
        raise CutoutUnavailable(f"Unknown cut-out model {chosen['model']!r}; use one of: {', '.join(MODELS)}.")
    return chosen


# --- the model ------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_path(name: str, cache_dir: Path | None = None, fetch: Callable[[str, Path], None] | None = None) -> Path:
    """The model file, downloaded on first use and always checked against its pinned SHA-256."""
    model = MODELS[name]
    cache_dir = Path(cache_dir or os.environ.get("FORGE_MODEL_DIR") or DEFAULT_CACHE)
    path = cache_dir / model.file
    if path.exists() and _sha256(path) == model.sha256:
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    try:
        (fetch or _download)(model.url, partial)
    except Exception as err:
        partial.unlink(missing_ok=True)
        raise CutoutUnavailable(f"Could not download the {name} cut-out model ({type(err).__name__}).") from None
    if _sha256(partial) != model.sha256:
        partial.unlink(missing_ok=True)
        raise CutoutUnavailable(f"The downloaded {name} cut-out model didn't match its pinned SHA-256, so it wasn't used.")
    partial.replace(path)
    return path


def _download(url: str, dest: Path) -> None:
    import requests

    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with dest.open("wb") as f:
            for chunk in response.iter_content(1 << 20):
                f.write(chunk)


class MaskMaker:
    """Runs one model. Call it with an RGB photo to get an "L" mask the same size (255 is subject)."""

    def __init__(self, name: str = DEFAULT_MODEL, cache_dir: Path | None = None):
        try:
            import onnxruntime as ort
        except ImportError:
            raise CutoutUnavailable('The cut-out needs onnxruntime: pip install -e ".[cutout]".') from None
        self.name = name
        self.model = MODELS[name]
        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(model_path(name, cache_dir)), options, providers=["CPUExecutionProvider"])

    def __call__(self, image: Image.Image) -> Image.Image:
        return raw_mask(self.session, self.model, image)


def model_input(model: Model, image: Image.Image) -> np.ndarray:
    """The photo as the model reads it: a square, scaled to 0..1, normalised, channels first."""
    square = np.asarray(image.convert("RGB").resize((model.size, model.size), Image.Resampling.LANCZOS), dtype=np.float32)
    square /= max(float(square.max()), 1e-6)
    square = (square - np.array(model.mean, dtype=np.float32)) / np.array(model.std, dtype=np.float32)
    return square.transpose(2, 0, 1)[None].astype(np.float32)


def to_mask(prediction: np.ndarray, model: Model, size: tuple[int, int]) -> Image.Image:
    """The model's answer as an 8-bit mask the size of the photo."""
    pred = np.squeeze(prediction).astype(np.float32)
    if model.sigmoid:
        pred = 1 / (1 + np.exp(-pred))
    low, high = float(pred.min()), float(pred.max())
    pred = (pred - low) / (high - low) if high > low else np.zeros_like(pred)
    return Image.fromarray((pred * 255).astype(np.uint8), mode="L").resize(size, Image.Resampling.LANCZOS)


def raw_mask(session, model: Model, image: Image.Image) -> Image.Image:
    name = session.get_inputs()[0].name
    output = session.run(None, {name: model_input(model, image)})[0]
    return to_mask(output[:, 0], model, image.size)


# --- cleaning ---------------------------------------------------------------------


def _small(mask: Image.Image, longest: int = 256) -> Image.Image:
    scale = longest / max(mask.size)
    if scale >= 1:
        return mask.copy()
    return mask.resize((max(1, round(mask.width * scale)), max(1, round(mask.height * scale))), Image.Resampling.BILINEAR)


def components(binary: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """Labels 4-connected regions of True. Returns the labels (0 is background) and each region's size,
    largest first, with labels renumbered to match (label 1 is the largest)."""
    h, w = binary.shape
    labels = np.zeros((h, w), dtype=np.int32)
    sizes: list[int] = []
    for y, x in zip(*np.nonzero(binary)):
        if labels[y, x]:
            continue
        label = len(sizes) + 1
        labels[y, x] = label
        queue, count = deque([(y, x)]), 0
        while queue:
            cy, cx = queue.popleft()
            count += 1
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and binary[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = label
                    queue.append((ny, nx))
        sizes.append(count)
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i])
    remap = np.zeros(len(sizes) + 1, dtype=np.int32)
    for new, old in enumerate(order, start=1):
        remap[old + 1] = new
    return remap[labels], [sizes[i] for i in order]


def _upscale(binary: np.ndarray, size: tuple[int, int]) -> Image.Image:
    return Image.fromarray(binary.astype(np.uint8) * 255, mode="L").resize(size, Image.Resampling.NEAREST)


def clean_mask(mask: Image.Image, speck: float = 0.02, hole: float = 0.002) -> Image.Image:
    """Drops anything under half opacity (faint leftovers), specks under `speck` of the main blob, and fills
    enclosed holes under `hole` of it. Real gaps, such as between a leg and a jeep, are bigger and stay."""
    firm = mask.point(lambda v: v if v >= 128 else 0)
    firm = firm.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))  # pinholes too small to see below
    small = np.asarray(_small(firm)) >= 128
    labels, sizes = components(small)
    if not sizes:
        return firm
    main = sizes[0]
    keep = np.isin(labels, [i + 1 for i, s in enumerate(sizes) if s >= speck * main])
    drop = small & ~keep
    if drop.any():
        gone = _upscale(drop, firm.size).filter(ImageFilter.MaxFilter(5))
        firm = ImageChops.subtract(firm, gone)
    background, gaps = components(~keep)
    edge = set(np.unique(np.concatenate([background[0], background[-1], background[:, 0], background[:, -1]])))
    holes = np.isin(background, [i + 1 for i, s in enumerate(gaps) if i + 1 not in edge and s < hole * main])
    if holes.any():
        firm = ImageChops.lighter(firm, _upscale(holes, firm.size).filter(ImageFilter.MaxFilter(5)))
    return firm


# --- checks -------------------------------------------------------------------------


@dataclass
class MaskCheck:
    """What check_mask found. `ok` is True only when nothing failed and at least one variant fits."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    stats: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        if self.ok:
            return "Pop-out possible: " + ", ".join(v.replace("_", " ") for v in self.variants) + "."
        return "No pop-out: " + "; ".join(self.reasons) + "."


def _margins(binary: np.ndarray) -> dict[str, float]:
    """How much of the photo lies beyond the subject on each side, as fractions."""
    rows, cols = np.nonzero(binary.any(axis=1))[0], np.nonzero(binary.any(axis=0))[0]
    h, w = binary.shape
    return {
        "top": float(rows[0] / h),
        "bottom": float((h - 1 - rows[-1]) / h),
        "left": float(cols[0] / w),
        "right": float((w - 1 - cols[-1]) / w),
    }


def fitting_variants(margins: dict[str, float], settings: dict) -> list[str]:
    """The ways out of the window the photo has room for. Stepping down needs ground under the subject;
    stepping sideways needs room on that side and some under it (so its foot isn't cut by the photo's
    edge); breaking the frame needs room all round."""
    room, ground = settings["min_margin"], settings["min_ground"]
    found = []
    if margins["bottom"] >= ground:
        found.append("step_down")
    for side in ("left", "right"):
        if margins[side] >= room and margins["bottom"] >= room:
            found.append(f"step_{side}")
    if all(margins[s] >= room for s in ("top", "bottom", "left", "right")):
        found.append("break_frame")
    return found


def check_mask(raw: Image.Image, cleaned: Image.Image, focus: Focus | None, settings: dict | None = None, need_box: bool = True) -> MaskCheck:
    """Gates 3 to 8 of the plan: one clear subject, where the analyst said it is, with firm edges and room to
    step out. `need_box` is False only when trying a loose file that the analyst hasn't seen."""
    settings = {**DEFAULTS, **(settings or {})}
    reasons: list[str] = []
    stats: dict[str, float] = {}
    if focus is None and need_box:
        reasons.append("the analyst gave no subject box")
    raw_px = np.asarray(raw)
    solid, soft = int((raw_px >= 224).sum()), int(((raw_px >= 32) & (raw_px < 224)).sum())
    stats["soft_edge"] = round(soft / max(1, solid + soft), 3)
    small = np.asarray(_small(cleaned)) >= 128
    area = float(small.mean())
    stats["area"] = round(area, 3)
    if not small.any():
        return MaskCheck(False, [*reasons, "the model found no subject"], [], stats)
    if area < settings["min_area"]:
        reasons.append(f"the subject is too small ({area:.0%} of the photo)")
    if area > settings["max_area"]:
        reasons.append(f"the subject fills too much of the photo ({area:.0%})")
    _, sizes = components(small)
    second = sizes[1] / sizes[0] if len(sizes) > 1 else 0.0
    stats["second_piece"] = round(second, 3)
    if second > settings["max_second_piece"]:
        reasons.append("the subject is in several pieces")
    if focus is not None:
        h, w = small.shape
        pad = 0.05  # the analyst's box is a rough guess
        top, bottom = max(0, int((focus.top - pad) * h)), min(h, int((focus.bottom + pad) * h) + 1)
        left, right = max(0, int((focus.left - pad) * w)), min(w, int((focus.right + pad) * w) + 1)
        inside = float(small[top:bottom, left:right].sum()) / float(small.sum())
        stats["box_overlap"] = round(inside, 3)
        if inside < settings["min_box_overlap"]:
            reasons.append("the cut-out isn't the subject the analyst described")
    if stats["soft_edge"] > settings["max_soft_edge"]:
        reasons.append("the subject's edges are unsure (fog, blur or glass)")
    margins = _margins(small)
    stats.update({f"margin_{k}": round(v, 3) for k, v in margins.items()})
    variants = fitting_variants(margins, settings)
    if not variants:
        reasons.append("the subject touches the photo's edges, so there's no room for it to step out")
    return MaskCheck(not reasons, reasons, variants if not reasons else [], stats)


@dataclass
class Cutout:
    """A cleaned mask and what the checks found. The photo itself is never part of this."""

    mask: Image.Image
    check: MaskCheck
    model: str


def cut_out(image: Image.Image, focus: Focus | None, settings: dict, maker: Callable[[Image.Image], Image.Image], need_box: bool = True) -> Cutout:
    """Makes, cleans and checks the mask for one photo."""
    raw = maker(image)
    if raw.size != image.size or raw.mode != "L":
        raw = raw.convert("L").resize(image.size)
    cleaned = clean_mask(raw)
    return Cutout(cleaned, check_mask(raw, cleaned, focus, settings, need_box), getattr(maker, "name", "custom"))


def subject_only(image: Image.Image, mask: Image.Image) -> Image.Image:
    """The photo's own pixels where the mask is, transparent elsewhere; for drafts and previews."""
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out
