"""One day's run for one profile.

Code steps fetch, prepare, post and keep score; AI steps (see agents/) decide what to post
and what to say. Every AI step has a safe fallback, so a bad AI answer never produces a
broken post: at worst the planner falls back to a random photo, or nothing is posted.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .agents import AIWorkflow, PlanItem, build_workflow
from .captions import CaptionError, CaptionRules, normalize_caption
from .config import Profile
from .hosting import ImageHost, build_host
from .imaging import PreparedImage, prepare_image
from .models import Caption, Photo, PostResult
from .publishers import InstagramPublisher, Publisher
from .sources import PhotoSource, build_source
from .state import ProfileState
from .usage import UsageLog

log = logging.getLogger(__name__)

DRAFTS_DIR = Path("drafts")
ANALYSIS_FIELDS = ("subject", "setting", "mood", "style", "colors", "season", "time_of_day", "quality_score", "themes")


class NothingToPost(Exception):
    pass


class ReviewRejected(Exception):
    pass


@dataclass
class Components:
    """Everything a run needs. Tests pass fakes; real runs build from the profile."""

    source: PhotoSource
    ai: AIWorkflow
    state: ProfileState
    host: ImageHost | None = None
    publisher: Publisher | None = None
    usage: UsageLog | None = None

    @classmethod
    def from_profile(cls, profile: Profile, *, publish: bool, command: str = "", with_insights: bool = False) -> "Components":
        usage = UsageLog(profile.name, pricing=profile.ai.get("pricing"), command=command)
        return cls(
            source=build_source(profile.source),
            ai=build_workflow(profile.ai, usage_log=usage),
            state=ProfileState(profile.name),
            host=build_host(profile.hosting) if publish else None,
            publisher=InstagramPublisher(profile.instagram) if (publish or with_insights) else None,
            usage=usage,
        )


class Run:
    def __init__(self, profile: Profile, components: Components, today: date | None = None):
        self.profile = profile
        self.c = components
        self.today = today or date.today()
        self._images: dict[str, PreparedImage] = {}
        self.notes: list[str] = []

    # --- code steps --------------------------------------------------------

    def refresh_insights(self) -> None:
        publisher = self.c.publisher
        if not hasattr(publisher, "fetch_insights"):
            return
        metrics = self.profile.instagram.get("insight_metrics", "reach,likes,comments,saved,shares")
        for media_id in self.c.state.media_needing_insights():
            try:
                self.c.state.save_insights(media_id, publisher.fetch_insights(media_id, metrics))
            except Exception as err:  # insights are a bonus; never block a post on them
                log.warning("Could not fetch insights for %s: %s", media_id, err)
                return

    def unposted(self) -> tuple[list[Photo], int]:
        photos = self.c.source.list_photos()
        unposted = [p for p in photos if not self.c.state.is_posted(p.id)]
        if not unposted and photos and self.profile.selection.get("when_exhausted", "stop") == "recycle":
            log.info("Every photo has been posted; starting a new cycle.")
            self.c.state.forget_posts()
            unposted = photos
        if not unposted:
            raise NothingToPost(
                f"All {len(photos)} photos for profile '{self.profile.name}' have been posted. Add more photos to the folder."
            )
        return unposted, len(photos)

    def image(self, photo: Photo) -> PreparedImage:
        if photo.id not in self._images:
            self._images[photo.id] = prepare_image(self.c.source.download(photo), self.profile.image)
        return self._images[photo.id]

    # --- AI steps ----------------------------------------------------------

    def analyse_new(self, photos: list[Photo]) -> None:
        budget = int(self.profile.ai.get("max_new_analyses_per_run", 20))
        for photo in photos:
            if self.c.state.analysis_for(photo.id) is not None:
                continue
            if budget <= 0:
                break
            budget -= 1
            try:
                analysis = self.c.ai.analyze(self.image(photo).preview_jpeg, photo_name=photo.name)
                self.c.state.save_analysis(photo.id, photo.name, analysis)
            except Exception as err:
                log.warning("Could not analyse %s: %s", photo.name, err)

    def plan(self, photos: list[Photo], horizon: int = 1) -> list[tuple[Photo, PlanItem]]:
        by_id = {p.id: p for p in photos}
        strategy = self.profile.selection.get("strategy", "ai_planner")
        if strategy == "ai_planner":
            limit = int(self.profile.selection.get("max_candidates", 60))
            pool = sorted(photos, key=lambda p: -(self.c.state.analysis_for(p.id) or {}).get("quality_score", 0))[:limit]
            candidates = []
            for p in pool:
                analysis = self.c.state.analysis_for(p.id) or {}
                candidates.append({"photo_id": p.id, "name": p.name, **{k: analysis.get(k) for k in ANALYSIS_FIELDS if k in analysis}})
            try:
                items = self.c.ai.plan(candidates, self.c.state.history(), today=self.today, horizon=horizon)
                chosen, seen = [], set()
                for item in items:
                    if item.photo_id in by_id and item.photo_id not in seen:
                        chosen.append((by_id[item.photo_id], item))
                        seen.add(item.photo_id)
                if chosen:
                    return chosen[:horizon]
                log.warning("The planner returned no usable photo IDs; falling back to random.")
            except Exception as err:
                log.warning("Planner failed (%s); falling back to random.", err)
            self.notes.append("The AI planner was unavailable, so today's photo was picked at random.")
        rng = random.Random(self.today.isoformat())
        if strategy == "ordered":
            order = sorted(photos, key=lambda p: (p.created_at is None, p.created_at or 0, p.name))
        else:
            order = sorted(photos, key=lambda p: p.id)
            rng.shuffle(order)
        return [(p, PlanItem(p.id, f"{strategy} pick", "")) for p in order[:horizon]]

    def compose(self, photo: Photo, plan_item: PlanItem) -> Caption:
        ai, state, settings = self.c.ai, self.c.state, self.profile.ai
        preview = self.image(photo).preview_jpeg
        analysis = state.analysis_for(photo.id) or {}
        rules = CaptionRules.from_settings(settings)
        max_revisions = int(settings.get("max_revisions", 1))
        feedback = ""
        last_problem = ""
        reviewed_draft: Caption | None = None
        for attempt in range(max_revisions + 1):
            draft = ai.write_caption(
                preview,
                analysis=analysis,
                angle=plan_item.angle,
                recent_captions=state.recent_captions(),
                top_captions=state.top_captions(),
                feedback=feedback,
            )
            draft.hashtags = ai.choose_hashtags(
                analysis=analysis,
                caption_text=draft.text,
                recent_tag_sets=state.recent_tag_sets(),
                tag_stats=state.tag_stats(),
                feedback=feedback,
            )
            try:
                caption = normalize_caption(draft, rules)
            except CaptionError as err:
                feedback = last_problem = f"The post broke a rule: {err}"
                log.warning("Attempt %d: %s", attempt + 1, last_problem)
                continue
            if not settings.get("review", True):
                return caption
            review = ai.review(preview, caption=caption)
            if review.approved:
                return caption
            reviewed_draft = caption
            feedback = last_problem = review.feedback or "The reviewer asked for a revision."
            log.info("Attempt %d sent back by the reviewer: %s", attempt + 1, feedback)
        if settings.get("on_review_reject", "skip") == "post" and reviewed_draft is not None:
            self.notes.append(f"Posted although the reviewer still had a concern: {last_problem}")
            return reviewed_draft
        raise ReviewRejected(f"No post today: the caption did not pass review. Last concern: {last_problem}")


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80]


def run(profile: Profile, components: Components, *, publish: bool, today: date | None = None, drafts_dir: Path = DRAFTS_DIR) -> PostResult:
    r = Run(profile, components, today)
    if components.publisher is not None:
        r.refresh_insights()
    unposted, total = r.unposted()
    log.info("%d of %d photos not posted yet", len(unposted), total)
    r.analyse_new(unposted)
    photo, plan_item = r.plan(unposted)[0]
    log.info("Planner chose %s: %s", photo.name, plan_item.reason)
    caption = r.compose(photo, plan_item)
    prepared = r.image(photo)

    result = PostResult(profile=profile.name, photo=photo, caption=caption, published=False, plan_reason=plan_item.reason)
    result.notes.extend(r.notes)
    if components.usage is not None:
        result.usage = components.usage.summary()
    if prepared.padded:
        result.notes.append("Photo was outside Instagram's 4:5 to 1.91:1 range, so a border was added.")

    if not publish:
        out = drafts_dir / profile.name / f"{r.today.isoformat()}_{_safe_name(photo.name)}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "image.jpg").write_bytes(prepared.jpeg)
        (out / "caption.txt").write_text(caption.render() + "\n", encoding="utf-8")
        (out / "alt_text.txt").write_text(caption.alt_text + "\n", encoding="utf-8")
        (out / "plan.txt").write_text(f"{plan_item.reason}\nAngle: {plan_item.angle}\n", encoding="utf-8")
        result.draft_dir = str(out)
        return result

    if components.host is None or components.publisher is None:
        raise RuntimeError("Publishing needs both an image host and a publisher.")
    key = f"{profile.name}-{_safe_name(photo.id)}.jpg"
    url = components.host.upload(key, prepared.jpeg)
    try:
        media_id = components.publisher.publish_image(url, caption.render(), caption.alt_text)
    finally:
        try:
            components.host.delete(key)
        except Exception:  # cleanup failure should not hide the real outcome
            log.warning("Could not delete temporary image %s", key)
    components.state.record_post(
        photo.id,
        photo_name=photo.name,
        media_id=media_id,
        caption=caption.render(),
        hashtags=caption.hashtags,
        plan_reason=plan_item.reason,
    )
    result.published = True
    result.media_id = media_id
    if components.usage is not None:
        result.usage = components.usage.summary()
    return result


def plan_days(profile: Profile, components: Components, days: int, today: date | None = None) -> list[tuple[Photo, PlanItem]]:
    """What the planner would post over the next few days. Posts nothing."""
    r = Run(profile, components, today)
    unposted, _ = r.unposted()
    r.analyse_new(unposted)
    return r.plan(unposted, horizon=days)
