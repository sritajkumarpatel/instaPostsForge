"""What each AI agent is asked to do. The pipeline only talks to this interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from ..models import Caption


@dataclass
class PlanItem:
    photo_id: str
    reason: str
    angle: str


@dataclass
class Review:
    approved: bool
    feedback: str = ""


class AIWorkflow(ABC):
    @abstractmethod
    def analyze(self, image_jpeg: bytes, *, photo_name: str) -> dict:
        """Photo analyst: describes one photo once, for every later step."""

    @abstractmethod
    def plan(self, candidates: list[dict], history: list[dict], *, today: date, horizon: int) -> list[PlanItem]:
        """Planner: orders unposted photos for the coming days. The first item is today's post."""

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
    ) -> Caption:
        """Caption writer: caption text, optional quote and alt text (hashtags left empty)."""

    @abstractmethod
    def choose_hashtags(
        self,
        *,
        analysis: dict,
        caption_text: str,
        recent_tag_sets: list[list[str]],
        tag_stats: dict[str, dict],
        feedback: str = "",
    ) -> list[str]:
        """Hashtag strategist: a small, varied, specific set of tags."""

    @abstractmethod
    def review(self, image_jpeg: bytes, *, caption: Caption, analysis: dict | None = None) -> Review:
        """Reviewer: approves the post or sends it back once with feedback."""
