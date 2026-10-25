"""Runs one day's post for one profile: pick, prepare, caption, publish, record."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .captions import CaptionRules, CaptionWriter, build_caption_writer, normalize_caption
from .captions.validator import CaptionError
from .config import Profile
from .hosting import ImageHost, build_host
from .imaging import prepare_image
from .models import PostResult
from .publishers import InstagramPublisher, Publisher
from .selection import pick_next
from .sources import PhotoSource, build_source  # noqa: F401
from .state import PostLog  # noqa: F401  (re-exported for the CLI)

log = logging.getLogger(__name__)

DRAFTS_DIR = Path("drafts")


class NothingToPost(Exception):
    pass


@dataclass
class Components:
    """Everything a run needs. Tests pass fakes; real runs build from the profile."""

    source: PhotoSource
    writer: CaptionWriter
    post_log: PostLog
    host: ImageHost | None = None
    publisher: Publisher | None = None

    @classmethod
    def from_profile(cls, profile: Profile, *, publish: bool) -> "Components":
        return cls(
            source=build_source(profile.source),
            writer=build_caption_writer(profile.captions),
            post_log=PostLog(profile.name),
            host=build_host(profile.hosting) if publish else None,
            publisher=InstagramPublisher(profile.instagram) if publish else None,
        )


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80]


def run(profile: Profile, components: Components, *, publish: bool, drafts_dir: Path = DRAFTS_DIR) -> PostResult:
    photos = components.source.list_photos()
    photo = pick_next(photos, components.post_log.posted_ids(), profile.selection)
    if photo is None:
        raise NothingToPost(f"All {len(photos)} photos for profile '{profile.name}' have been posted. Add more.")
    log.info("Picked %s (%d unposted left)", photo.name, len(photos) - len(components.post_log.posted_ids()) - 1)

    prepared = prepare_image(components.source.download(photo), profile.image)
    rules = CaptionRules.from_settings(profile.captions)
    caption = None
    last_error: CaptionError | None = None
    for _ in range(2):  # one retry if the first caption breaks a rule
        draft = components.writer.write(
            prepared.preview_jpeg, photo_name=photo.name, recent_captions=components.post_log.recent_captions()
        )
        try:
            caption = normalize_caption(draft, rules)
            break
        except CaptionError as err:
            last_error = err
            log.warning("Caption rejected, retrying: %s", err)
    if caption is None:
        raise last_error or CaptionError("No caption produced.")

    result = PostResult(profile=profile.name, photo=photo, caption=caption, published=False)
    if prepared.padded:
        result.notes.append("Photo was outside Instagram's 4:5 to 1.91:1 range, so a border was added.")

    if not publish:
        out = drafts_dir / profile.name / f"{date.today().isoformat()}_{_safe_name(photo.name)}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "image.jpg").write_bytes(prepared.jpeg)
        (out / "caption.txt").write_text(caption.render() + "\n", encoding="utf-8")
        (out / "alt_text.txt").write_text(caption.alt_text + "\n", encoding="utf-8")
        result.draft_dir = str(out)
        return result

    if components.host is None or components.publisher is None:
        raise RuntimeError("Publishing needs both an image host and a publisher.")
    key = f"{profile.name}/{_safe_name(photo.id)}.jpg"
    url = components.host.upload(key, prepared.jpeg)
    try:
        media_id = components.publisher.publish_image(url, caption.render(), caption.alt_text)
    finally:
        try:
            components.host.delete(key)
        except Exception:  # cleanup failure should not hide the real outcome
            log.warning("Could not delete temporary image %s", key)
    components.post_log.record(photo.id, photo.name, media_id, caption.render())
    result.published = True
    result.media_id = media_id
    return result
