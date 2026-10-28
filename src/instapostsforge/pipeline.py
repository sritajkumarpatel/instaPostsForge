"""One day's run for one profile.

Code steps fetch, prepare, post and keep score; AI steps (see agents/) decide what to post
and what to say. Every AI step has a safe fallback, so a bad AI answer never produces a
broken post: at worst the planner falls back to a random photo, or nothing is posted.

A day's post is one photo, or, when multi-photo posts are on, possibly 2 to 5 photos of the
same shape from one outing or place (see grouping.py). Code proposes the sets; the planner decides.
"""

from __future__ import annotations

import logging
import os
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .agents import AIWorkflow, PlanItem, SetPhoto, build_workflow
from .agents.playbooks import NO_QUOTE_CATEGORIES
from .captions import CaptionError, CaptionRules, normalize_caption, trim_caption_text
from .config import Profile
from .hosting import ImageHost, build_host
from .grouping import PhotoSet, propose_sets
from .imaging import PreparedImage, fingerprint, fingerprint_distance, focus_from, name_time, prepare_image
from .models import Caption, Photo, PostResult
from .publishers import InstagramPublisher, Publisher
from .sources import PhotoSource, build_source
from .state import ProfileState
from .state.store import photo_key
from .usage import UsageLog

log = logging.getLogger(__name__)

DRAFTS_DIR = Path("drafts")
ANALYSIS_FIELDS = (
    "category", "mood", "subject", "main_subject", "orientation", "shape", "setting", "place_name", "taken_at", "colors", "season", "time_of_day", "quality_score", "themes",
)
# Reviewer checklist items serious enough to skip a day: a false claim about the photo, a wrongly
# attributed quote, or a safety problem (explicit content, an address). Everything else is style.
HARD_CHECKS = frozenset({"accuracy", "quote", "safety"})
NEAR_DUPLICATE_DISTANCE = 6  # fingerprint bits that may differ for two photos to count as near-duplicates
TAKEN_AT_FORMAT = "%A %d %B %Y, %H:%M"
# Instagram shows every photo of a multi-photo post in the cover's shape, so a set only holds photos
# whose own width:height matches to within this much: they go out as they are, with no added border.
SAME_SHAPE_TOLERANCE = 0.01


def same_shape(a: float | None, b: float | None) -> bool:
    return bool(a and b) and abs(a / b - 1) <= SAME_SHAPE_TOLERANCE


def orientation(shape: float) -> str:
    return "portrait" if shape < 0.95 else "landscape" if shape > 1.05 else "square"
OFF_WORDS = {"false", "0", "no", "off"}


def multi_photo_settings(profile: Profile) -> dict:
    """selection.multi_photo, with the MULTI_PHOTO environment variable (a GitHub variable in
    Actions) overriding its enabled switch, so multi-photo posts can be turned off without a commit."""
    settings = dict(profile.selection.get("multi_photo") or {})
    settings["switched_by"] = "selection.multi_photo.enabled in the config"
    switch = os.environ.get("MULTI_PHOTO", "").strip().strip("\"'").strip().lower()  # False, " false", "'false'"
    if switch:
        settings["enabled"] = switch not in OFF_WORDS
        settings["switched_by"] = f"MULTI_PHOTO={switch}"
    settings.setdefault("enabled", False)
    settings["max_photos"] = max(2, min(10, int(settings.get("max_photos", 5))))
    return settings


def subject_word(subject: str | None) -> str:
    """The analyst's main subject in one comparable form: "Full Moon " and "full moon" match."""
    return " ".join((subject or "").lower().split())


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
        ai = build_workflow(profile.ai, usage_log=usage)  # first, so a missing AI key stops the run before anything else
        return cls(
            source=build_source(profile.source),
            ai=ai,
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

    def post_image(self, photo: Photo) -> PreparedImage:
        """The image as it goes out. A trimmed photo is prepared again with the subject band the analyst
        reported, so the strip comes from where the subject isn't; without one it stays centred."""
        image = self.image(photo)
        focus = focus_from(self.c.state.analysis_for(photo.id))
        if not image.cropped or focus is None:
            return image
        return prepare_image(self.c.source.download(photo), self.profile.image, focus)

    def taken_at(self, photo: Photo) -> str | None:
        """When the photo was taken, from EXIF or the file name, as the AI steps see it."""
        when = photo.taken_at or self.image(photo).taken_at or name_time(photo.name)
        return when.strftime(TAKEN_AT_FORMAT) if when else None

    def known_time(self, photo: Photo) -> datetime | None:
        """The capture time without downloading the photo: source metadata, the analyst's notes or the file name."""
        if photo.taken_at:
            return photo.taken_at
        noted = (self.c.state.analysis_for(photo.id) or {}).get("taken_at")
        if noted:
            try:
                return datetime.strptime(noted, TAKEN_AT_FORMAT)
            except ValueError:
                pass
        return name_time(photo.name)

    # --- AI steps ----------------------------------------------------------

    def analyse_new(self, photos: list[Photo]) -> None:
        budget = int(self.profile.ai.get("max_new_analyses_per_run", 20))
        for photo in photos:
            existing = self.c.state.analysis_for(photo.id)
            if existing is not None and "category" in existing and "main_subject" in existing:
                if "shape" not in existing and budget > 0:  # added later: measured by code, no AI call
                    budget -= 1
                    try:
                        shape = self.image(photo).shape
                        updated = {**existing, "shape": shape, "orientation": orientation(shape)}
                        self.c.state.save_analysis(photo.id, photo.name, updated, self.c.state.fingerprint_for(photo.id))
                    except Exception as err:
                        log.warning("Could not measure %s: %s", photo.name, err)
                continue  # older analyses, from before categories or main subjects existed, are redone once
            if budget <= 0:
                break
            budget -= 1
            try:
                preview = self.image(photo).preview_jpeg
                analysis = self.c.ai.analyze(preview, photo_name=photo.name)
                if taken := self.taken_at(photo):
                    analysis["taken_at"] = taken  # from metadata, not the model, so later steps can trust it
                analysis["shape"] = self.image(photo).shape
                analysis["orientation"] = orientation(analysis["shape"])
                self.c.state.save_analysis(photo.id, photo.name, analysis, fingerprint(preview))
            except Exception as err:
                log.warning("Could not analyse %s: %s", photo.name, err)

    # --- content rules (code, so they hold whatever the AI says) ---------------

    def eligible(self, photos: list[Photo]) -> list[Photo]:
        """Drops photos that must not be posted (now), and low-quality ones while better remain."""
        rules = self.profile.selection
        recent_prints = self.c.state.recent_fingerprints(int(rules.get("near_duplicate_days", 30)))
        skipped: dict[str, int] = {}
        allowed = []
        for photo in photos:
            analysis = self.c.state.analysis_for(photo.id) or {}
            reason = None
            if analysis.get("is_explicit"):
                reason = "explicit or nude content"
            elif analysis.get("is_screenshot_or_meme") and rules.get("skip_screenshots", True):
                reason = "screenshots or memes"
            elif analysis.get("has_children") and not rules.get("allow_children", False):
                reason = "children in the photo (set selection.allow_children to post these)"
            elif (fp := self.c.state.fingerprint_for(photo.id)) and any(
                fingerprint_distance(fp, other) <= NEAR_DUPLICATE_DISTANCE for other in recent_prints
            ):
                reason = "near-duplicates of a recent post"
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
            else:
                allowed.append(photo)
        for reason, count in skipped.items():
            log.info("Skipping %d photo(s): %s", count, reason)
        if not allowed:
            detail = "; ".join(f"{n} {r}" for r, n in skipped.items())
            raise NothingToPost(f"{len(photos)} unposted photos, but none can be posted: {detail}. Add more photos to the folder.")
        floor = int(rules.get("min_quality", 4))
        good = [p for p in allowed if (self.c.state.analysis_for(p.id) or {}).get("quality_score", floor) >= floor]
        if good:
            return good
        self.notes.append(f"Every remaining photo scored below {floor} for quality, so a low-scoring one was posted.")
        return allowed

    def members(self, entry: tuple[Photo, PlanItem]) -> list[str]:
        return [entry[0].id, *entry[1].extra_ids]

    def keep_variety(self, order: list[tuple[Photo, PlanItem]], horizon: int) -> list[tuple[Photo, PlanItem]]:
        """Reorders so no main subject repeats within a few days and no mood exceeds its weekly limit
        (and, when no_repeat_category is on, no category follows the last post or yesterday's). If nothing fits, the planner's next pick goes ahead.
        A multi-photo post counts with its cover's category and mood and every photo's subject."""
        planning = self.profile.ai.get("planning") or {}
        no_repeat = planning.get("no_repeat_category", False)
        subject_days = int(planning.get("no_repeat_subject_days", 3))
        max_mood = int(planning.get("max_same_mood_per_7_days", 2))
        max_multi = int(multi_photo_settings(self.profile).get("max_per_7_days", 3))
        history = self.c.state.history(limit=max(6, subject_days))
        # Today's post must not share a category with the last post, nor with anything posted
        # yesterday or earlier today (a second run in one day still counts against yesterday).
        since = (self.today - timedelta(days=1)).isoformat()
        last_categories = {h.get("category") for h in history[:1]} | {h.get("category") for h in history if (h.get("posted_at") or "") >= since}
        recent_moods = [h.get("mood") for h in history]  # newest first; with today, a 7-day window
        recent_subjects = [{subject_word(s) for s in h.get("all_subjects") or [h.get("main_subject")]} for h in history]
        recent_multi = [h.get("photo_count", 1) > 1 for h in history]
        remaining = list(order)
        chosen, used = [], set()
        while remaining and len(chosen) < horizon:
            remaining = [e for e in remaining if not used & set(self.members(e))]  # a photo goes out once
            if not remaining:
                break

            def fits(entry):
                analysis = self.c.state.analysis_for(entry[0].id) or {}
                category, mood = analysis.get("category"), analysis.get("mood")
                if no_repeat and category and category in last_categories:
                    return False
                taken = set().union(*recent_subjects[:subject_days])
                if any(subject_word((self.c.state.analysis_for(i) or {}).get("main_subject")) in taken - {""} for i in self.members(entry)):
                    return False
                return not mood or recent_moods[:6].count(mood) < max_mood

            pick = next((e for e in remaining if fits(e)), remaining[0])
            if pick is not remaining[0]:
                log.info("Moved %s up to keep the feed varied.", pick[0].name)
            remaining.remove(pick)
            if pick[1].extra_ids and sum(recent_multi[:6]) >= max_multi:
                log.info("Posting %s on its own: %d multi-photo posts already in the last 7.", pick[0].name, max_multi)
                pick[1].extra_ids = []
            chosen.append(pick)
            used.update(self.members(pick))
            analysis = self.c.state.analysis_for(pick[0].id) or {}
            last_categories = {analysis.get("category")}
            recent_moods.insert(0, analysis.get("mood"))
            recent_subjects.insert(0, {subject_word((self.c.state.analysis_for(i) or {}).get("main_subject")) for i in self.members(pick)})
            recent_multi.insert(0, bool(pick[1].extra_ids))
        return chosen

    def photo_sets(self, pool: list[Photo]) -> list[PhotoSet]:
        """Sets of photos that could go out together: split by shape, so every photo in a set has the
        same width:height and nothing needs a border, and with near-identical frames removed."""
        settings = multi_photo_settings(self.profile)
        analyses = {p.id: self.c.state.analysis_for(p.id) or {} for p in pool}
        times = {p.id: t for p in pool if (t := self.known_time(p))}
        place_names = {
            p.id: name.strip().lower()
            for p in pool
            if (name := analyses[p.id].get("place_name")) and not analyses[p.id].get("shows_private_home")
        }
        quality = {p.id: analyses[p.id].get("quality_score", 0) for p in pool}
        sets = []
        for found in propose_sets(
            pool,
            times=times,
            place_names=place_names,
            quality=quality,
            hours=float(settings.get("group_hours", 3)),
            km=float(settings.get("group_km", 2)),
        ):
            kept: list[Photo] = []
            for photo in found.photos:  # best first, so the better of two near-identical frames stays
                fp = self.c.state.fingerprint_for(photo.id)
                if fp and any(
                    (other := self.c.state.fingerprint_for(k.id)) and fingerprint_distance(fp, other) <= NEAR_DUPLICATE_DISTANCE for k in kept
                ):
                    continue
                kept.append(photo)
            by_shape: list[list[Photo]] = []
            for photo in kept:
                shape = analyses[photo.id].get("shape")
                group = next((g for g in by_shape if same_shape(shape, analyses[g[0].id].get("shape"))), None)
                if group:
                    group.append(photo)
                elif shape:  # a photo whose shape isn't measured yet waits for a later run
                    by_shape.append([photo])
            for n, group in enumerate(g for g in by_shape if len(g) >= 2):
                sets.append(PhotoSet(f"{found.set_id}{'abcdefghij'[n]}" if n else found.set_id, found.kind, group))
        return sets

    def check_extras(self, item: PlanItem, sets: list[PhotoSet], allowed: set[str]) -> list[str]:
        """The planner's extra photos, kept only if they come from one set with the cover (so they have
        its shape) and may be posted."""
        best: list[str] = []
        for photo_set in sets:
            if item.photo_id not in photo_set.ids:
                continue
            extras = [i for i in dict.fromkeys(item.extra_ids) if i in photo_set.ids and i != item.photo_id and i in allowed]
            if len(extras) > len(best):
                best = extras
        limit = multi_photo_settings(self.profile)["max_photos"] - 1
        if len(best) < len(item.extra_ids):
            log.info("Dropped %d extra photo(s) the planner named outside the cover's set.", len(item.extra_ids) - len(best))
        return best[:limit]

    def plan(self, photos: list[Photo], horizon: int = 1) -> list[tuple[Photo, PlanItem]]:
        photos = self.eligible(photos)
        by_id = {p.id: p for p in photos}
        rng = random.Random(self.today.isoformat())
        strategy = self.profile.selection.get("strategy", "ai_planner")
        if strategy == "ai_planner":
            limit = int(self.profile.selection.get("max_candidates", 60))
            pool = sorted(photos, key=lambda p: (-(self.c.state.analysis_for(p.id) or {}).get("quality_score", 0), p.id))[:limit]
            rng.shuffle(pool)  # so the planner doesn't always read the same photos first
            candidates = []
            for p in pool:
                analysis = self.c.state.analysis_for(p.id) or {}
                candidates.append({"photo_id": p.id, "name": p.name, **{k: analysis.get(k) for k in ANALYSIS_FIELDS if k in analysis}})
            multi = multi_photo_settings(self.profile)
            log.info("Multi-photo posts are %s (%s).", "on" if multi["enabled"] else "off", multi["switched_by"])
            sets = self.photo_sets(pool) if multi["enabled"] else []
            rng.shuffle(sets)
            brief_sets = None
            if sets:
                history = self.c.state.history(limit=7)
                brief_sets = {
                    "max_photos_per_post": multi["max_photos"],
                    "multi_photo_posts_in_last_7": sum(1 for h in history if h.get("photo_count", 1) > 1),
                    "sets": [{"set_id": x.set_id, "kind": x.kind, "photo_ids": x.ids} for x in sets],
                }
            try:
                items = self.c.ai.plan(
                    candidates, self.c.state.history(), today=self.today, horizon=horizon, photo_sets=brief_sets
                )
                chosen, seen = [], set()
                for item in items:
                    if item.photo_id in by_id and item.photo_id not in seen:
                        item.extra_ids = self.check_extras(item, sets, set(by_id) - seen) if item.extra_ids else []
                        chosen.append((by_id[item.photo_id], item))
                        seen.add(item.photo_id)
                        seen.update(item.extra_ids)
                if chosen:
                    # Photos the planner left out stay available as backups for the variety rules.
                    backups = [p for p in sorted(photos, key=lambda p: p.id) if p.id not in seen]
                    rng.shuffle(backups)
                    chosen += [(p, PlanItem(p.id, "Picked to keep the feed varied.", "")) for p in backups]
                    return self.keep_variety(chosen, horizon)
                log.warning("The planner returned no usable photo IDs; falling back to random.")
            except Exception as err:
                log.warning("Planner failed (%s); falling back to random.", err)
            self.notes.append("The AI planner was unavailable, so today's photo was picked at random.")
        if strategy == "ordered":
            order = sorted(photos, key=lambda p: (p.created_at is None, p.created_at or 0, p.name))
            return [(p, PlanItem(p.id, "ordered pick", "")) for p in order[:horizon]]
        order = sorted(photos, key=lambda p: p.id)
        rng.shuffle(order)
        return self.keep_variety([(p, PlanItem(p.id, f"{strategy} pick", "")) for p in order], horizon)

    def post_images(self, photo: Photo, more: list[Photo]) -> list[bytes]:
        """The JPEGs to post, cover first. Sets only hold photos of one shape, so each goes out as it is."""
        images = [self.post_image(p) for p in [photo, *more]]
        if any(not same_shape(images[0].shape, i.shape) for i in images[1:]):
            raise RuntimeError("A multi-photo post must have photos of one shape; refusing to crop or add borders.")
        return [i.jpeg for i in images]

    def notes_for(self, photo: Photo) -> dict:
        """The analyst's notes as the writing steps see them."""
        analysis = dict(self.c.state.analysis_for(photo.id) or {})
        if "taken_at" not in analysis and (taken := self.taken_at(photo)):
            analysis["taken_at"] = taken
        if analysis.get("shows_private_home"):
            analysis["place_name"] = None  # never name the location of a private home
        return analysis

    def compose(self, photo: Photo, plan_item: PlanItem, more: list[Photo] | None = None) -> Caption:
        ai, state, settings = self.c.ai, self.c.state, self.profile.ai
        preview = self.image(photo).preview_jpeg
        analysis = self.notes_for(photo)
        more_photos = [SetPhoto(self.image(p).preview_jpeg, self.notes_for(p)) for p in more or []] or None
        rules = CaptionRules.from_settings(settings)
        max_revisions = int(settings.get("max_revisions", 2))
        feedback = ""
        last_problem = ""
        reviewed_draft: Caption | None = None
        draft: Caption | None = None
        tags: list[str] = []
        redo_caption = redo_tags = True
        first_failed: set[str] | None = None  # what the first review faulted; later rounds may only re-raise these
        hard_problem = False  # whether the last review raised a concern that must stop the post

        def write(note: str) -> Caption:
            text = ai.write_caption(
                preview,
                analysis=analysis,
                angle=plan_item.angle,
                recent_captions=state.recent_captions(),
                top_captions=state.top_captions(),
                feedback=note,
                **({"more_photos": more_photos} if more_photos else {}),
            )
            if text.quote and analysis.get("category") in NO_QUOTE_CATEGORIES and settings.get("quote", "when_fitting") == "when_fitting":
                text.quote = text.quote_author = None
            return text

        for attempt in range(max_revisions + 1):
            # Only the part the reviewer faulted is redone, so a fix to one part can't break another.
            if redo_caption or draft is None:
                draft = write(feedback)
                if len(draft.text.strip()) > rules.max_text_chars:
                    # One quick shortening pass by the writer, which knows what each sentence covers; it
                    # doesn't count as a review round. Code trims only what is still too long after it.
                    over = len(draft.text.strip())
                    log.info("The caption is %d characters; asking the writer to shorten it.", over)
                    draft = write(
                        (feedback + "\n\n" if feedback else "")
                        + f"length: the caption text is {over} characters; the limit is {rules.max_text_chars}. "
                        "Shorten it, keeping the hook, the closing question and a mention of every photo."
                    )
                trimmed = trim_caption_text(draft.text, rules.max_text_chars)
                if trimmed is None:
                    feedback = last_problem = (
                        f"length: the first sentence alone is over {rules.max_text_chars} characters. "
                        f"Write a shorter hook and keep the whole caption under {rules.max_text_chars} characters."
                    )
                    log.info("Attempt %d: %s", attempt + 1, last_problem)
                    redo_caption, redo_tags = True, False
                    continue
                if trimmed != draft.text.strip():
                    log.info("Trimmed the caption from %d to %d characters.", len(draft.text.strip()), len(trimmed))
                    draft.text = trimmed
            if redo_tags or not tags:
                tags = ai.choose_hashtags(
                    analysis=analysis,
                    caption_text=draft.text,
                    recent_tag_sets=state.recent_tag_sets(),
                    tag_stats=state.tag_stats(),
                    feedback=feedback,
                    **({"more_analyses": [p.analysis for p in more_photos]} if more_photos else {}),
                )
            draft.hashtags = list(tags)
            try:
                caption = normalize_caption(draft, rules)
            except CaptionError as err:
                feedback = last_problem = f"The post broke a rule: {err}"
                log.warning("Attempt %d: %s", attempt + 1, last_problem)
                redo_caption = redo_tags = True
                continue
            if not settings.get("review", True):
                return caption
            review = ai.review(preview, caption=caption, analysis=analysis, **({"more_photos": more_photos} if more_photos else {}))
            # Code has already held the caption to its length limit, so a length complaint alone doesn't block it.
            failed = set(review.failed) - {"length"}
            if first_failed is None:
                first_failed = set(failed)
            else:
                # No moving goalposts: after the first round only its own concerns and hard problems count.
                ignored = failed - first_failed - HARD_CHECKS
                if ignored:
                    log.info("Attempt %d: treating new %s notes as suggestions only.", attempt + 1, ", ".join(sorted(ignored)))
                failed -= ignored
            if review.approved or (review.failed and not failed):
                return caption
            reviewed_draft = caption
            hard_problem = not failed or bool(failed & HARD_CHECKS)
            feedback = last_problem = review.feedback or "The reviewer asked for a revision."
            redo_tags = not failed or "hashtags" in failed
            redo_caption = not failed or bool(failed - {"hashtags"})
            log.info("Attempt %d sent back by the reviewer: %s", attempt + 1, feedback)
        mode = settings.get("on_review_reject", "skip_if_serious")
        if reviewed_draft is not None and (mode == "post" or (mode == "skip_if_serious" and not hard_problem)):
            self.notes.append(f"Posted although the reviewer still had a style concern: {last_problem}")
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
    by_id = {p.id: p for p in unposted}
    more = [by_id[i] for i in plan_item.extra_ids]
    if more:
        log.info("Planner chose a multi-photo post of %d, cover %s: %s", 1 + len(more), photo.name, plan_item.reason)
    else:
        log.info("Planner chose %s: %s", photo.name, plan_item.reason)
    caption = r.compose(photo, plan_item, more)
    jpegs = r.post_images(photo, more)

    result = PostResult(
        profile=profile.name, photo=photo, caption=caption, published=False, plan_reason=plan_item.reason, more_photos=more
    )
    result.notes.extend(r.notes)
    if components.usage is not None:
        result.usage = components.usage.summary()
    if any(r.image(p).padded for p in [photo, *more]):
        result.notes.append("A photo was outside Instagram's 4:5 to 1.91:1 range, so a border was added.")

    if not publish:
        out = drafts_dir / profile.name / f"{r.today.isoformat()}_{_safe_name(photo.name)}"
        out.mkdir(parents=True, exist_ok=True)
        if more:
            for n, jpeg in enumerate(jpegs, 1):
                (out / f"{n}.jpg").write_bytes(jpeg)
        else:
            (out / "image.jpg").write_bytes(jpegs[0])
        (out / "caption.txt").write_text(caption.render() + "\n", encoding="utf-8")
        (out / "alt_text.txt").write_text("\n".join([caption.alt_text, *caption.more_alt_texts]) + "\n", encoding="utf-8")
        (out / "plan.txt").write_text(f"{plan_item.reason}\nAngle: {plan_item.angle}\n", encoding="utf-8")
        result.draft_dir = str(out)
        return result

    if components.host is None or components.publisher is None:
        raise RuntimeError("Publishing needs both an image host and a publisher.")
    keys = [f"{profile.name}-{photo_key(p.id)[len('h:'):]}.jpg" for p in [photo, *more]]  # never the Drive ID: the repo is public
    uploaded: list[str] = []
    try:
        urls = []
        for key, jpeg in zip(keys, jpegs):
            urls.append(components.host.upload(key, jpeg))
            uploaded.append(key)
        if more:
            media_id = components.publisher.publish_carousel(urls, caption.render(), [caption.alt_text, *caption.more_alt_texts])
        else:
            media_id = components.publisher.publish_image(urls[0], caption.render(), caption.alt_text)
    finally:
        for key in uploaded:
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
        more_photo_ids=[p.id for p in more],
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
