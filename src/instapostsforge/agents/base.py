"""What each AI agent is asked to do. The pipeline only talks to this interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from ..models import Caption


@dataclass
class PlanItem:
    photo_id: str  # the photo to post, or the cover (first photo) of a multi-photo post
    reason: str
    angle: str
    extra_ids: list[str] = field(default_factory=list)  # the other photos of a multi-photo post, in order


@dataclass
class SetPhoto:
    """One more photo of a multi-photo post, as the caption writer and reviewer see it."""

    image_jpeg: bytes
    analysis: dict


@dataclass
class Review:
    approved: bool
    feedback: str = ""
    failed: list[str] = field(default_factory=list)  # checklist items that failed, e.g. ["hashtags"]


class AIWorkflow(ABC):
    @abstractmethod
    def analyze(self, image_jpeg: bytes, *, photo_name: str) -> dict:
        """Photo analyst: describes one photo once, for every later step."""

    @abstractmethod
    def plan(
        self, candidates: list[dict], history: list[dict], *, today: date, horizon: int, photo_sets: dict | None = None
    ) -> list[PlanItem]:
        """Planner: orders unposted photos for the coming days. The first item is today's post.
        When photo_sets is given, an item may instead be 2 or more photos of one set, posted together."""

    @abstractmethod
    def write_caption(
        self,
        image_jpeg: bytes,
        *,
        analysis: dict,
        angle: str,
        recent_captions: list[str],
        top_captions: list[str],
        feedback: str = "",
        more_photos: list[SetPhoto] | None = None,
        style_brief: str = "",
    ) -> Caption:
        """Caption writer: caption text, optional quote and alt text (hashtags left empty).
        For a multi-photo post, image_jpeg and analysis are the cover and more_photos the rest, in order.
        For a styled post, style_brief says what text goes on the image; it comes back in Caption.overlay."""

    @abstractmethod
    def choose_hashtags(
        self,
        *,
        analysis: dict,
        caption_text: str,
        recent_tag_sets: list[list[str]],
        tag_stats: dict[str, dict],
        feedback: str = "",
        more_analyses: list[dict] | None = None,
    ) -> list[str]:
        """Hashtag strategist: a small, varied, specific set of tags."""

    @abstractmethod
    def review(
        self, image_jpeg: bytes, *, caption: Caption, analysis: dict | None = None, more_photos: list[SetPhoto] | None = None
    ) -> Review:
        """Reviewer: approves the post, or sends it back with feedback and the checklist items that failed."""
