"""The five agents, backed by Claude. Prompts live in instapostsforge/prompts/*.md."""

from __future__ import annotations

import base64
import json
import logging
from datetime import date
from importlib import resources
from string import Template
from typing import Literal, TypeVar

import anthropic
from pydantic import BaseModel, Field

from ..models import Caption
from .base import AIWorkflow, PlanItem, Review

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

QUOTE_RULES = {
    "always": "Always include one quote that fits the mood of the photo.",
    "when_fitting": "Include a quote only when one genuinely fits the photo; otherwise leave it out.",
    "never": "Do not include a quote.",
}


class PhotoAnalysis(BaseModel):
    subject: str = Field(description="The main subject in a few words.")
    description: str = Field(description="Two or three sentences on what is in the frame.")
    setting: str = Field(description="Where it was taken, or the kind of place if not recognisable.")
    mood: str
    colors: list[str]
    style: str = Field(description="Photographic style, e.g. street, landscape, macro, portrait.")
    time_of_day: str | None = None
    season: str | None = None
    quality_score: int = Field(ge=1, le=10)
    themes: list[str] = Field(description="Themes and audiences, specific to broad.")


class PlannedPhoto(BaseModel):
    photo_id: str
    reason: str
    angle: str


class Plan(BaseModel):
    order: list[PlannedPhoto]


class CaptionDraft(BaseModel):
    caption: str = Field(description="Caption text without hashtags; opens with a short hook line.")
    quote: str | None = None
    quote_author: str | None = None
    quote_is_attributed: bool = Field(default=False, description="True only if certain who said the quote.")
    alt_text: str


class ChosenTag(BaseModel):
    tag: str
    role: Literal["specific", "place", "niche", "community"]
    reason: str


class HashtagSet(BaseModel):
    hashtags: list[ChosenTag]


class ReviewResult(BaseModel):
    verdict: Literal["approve", "revise"]
    feedback: str = Field(default="", description="Specific changes to make when the verdict is revise.")


def load_prompt(name: str) -> str:
    return resources.files("instapostsforge.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


def image_block(image_jpeg: bytes) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(image_jpeg).decode("ascii")},
    }


class ClaudeWorkflow(AIWorkflow):
    def __init__(self, settings: dict, client: anthropic.Anthropic | None = None):
        self.settings = settings
        self.model = settings.get("model", "claude-opus-5-5")
        self.client = client or anthropic.Anthropic()
        notes = []
        if settings.get("account_theme"):
            notes.append(f"The account's overall theme: {settings['account_theme']}")
        if settings.get("extra_instructions"):
            notes.append(f"Additional instructions from the account owner: {settings['extra_instructions']}")
        self.account_notes = ("\n" + "\n".join(notes)) if notes else ""

    def _ask(self, system: str, content: list[dict], schema: type[T]) -> T:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"The model declined the {schema.__name__} request.")
        if response.parsed_output is None:
            raise RuntimeError(f"No usable {schema.__name__} returned (stop reason: {response.stop_reason}).")
        return response.parsed_output

    def analyze(self, image_jpeg: bytes, *, photo_name: str) -> dict:
        result = self._ask(
            load_prompt("analyst"),
            [image_block(image_jpeg), {"type": "text", "text": f"File name: {photo_name}\n\nAnalyse this photo."}],
            PhotoAnalysis,
        )
        return result.model_dump()

    def plan(self, candidates: list[dict], history: list[dict], *, today: date, horizon: int) -> list[PlanItem]:
        brief = {
            "today": today.isoformat(),
            "weekday": today.strftime("%A"),
            "plan_this_many_days": horizon,
            "unposted_photos": candidates,
            "recent_posts_newest_first": history,
        }
        result = self._ask(
            load_prompt("planner"),
            [{"type": "text", "text": json.dumps(brief, ensure_ascii=False, indent=1)}],
            Plan,
        )
        return [PlanItem(p.photo_id, p.reason, p.angle) for p in result.order]

    def write_caption(self, image_jpeg, *, analysis, angle, recent_captions, top_captions, feedback=""):
        system = Template(load_prompt("caption_writer")).safe_substitute(
            quote_rule=QUOTE_RULES.get(self.settings.get("quote", "when_fitting"), QUOTE_RULES["when_fitting"]),
            language=self.settings.get("language", "English"),
            account_notes=self.account_notes,
        )
        text = (
            f"Analyst's notes:\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
            f"Angle chosen by the planner: {angle or 'your choice'}\n\n"
            f"Recent captions (do not repeat their openings):\n" + ("\n---\n".join(recent_captions) or "(none yet)") + "\n\n"
            f"Captions that performed best so far:\n" + ("\n---\n".join(top_captions) or "(no results yet)")
        )
        if feedback:
            text += f"\n\nThe reviewer sent the previous draft back with this feedback:\n{feedback}"
        draft = self._ask(system, [image_block(image_jpeg), {"type": "text", "text": text}], CaptionDraft)
        return Caption(
            text=draft.caption,
            hashtags=[],
            alt_text=draft.alt_text,
            quote=draft.quote,
            quote_author=draft.quote_author if draft.quote_is_attributed else None,
        )

    def choose_hashtags(self, *, analysis, caption_text, recent_tag_sets, tag_stats, feedback=""):
        tags = self.settings.get("hashtags") or {}
        system = Template(load_prompt("hashtags")).safe_substitute(
            min_tags=tags.get("min", 3), max_tags=tags.get("max", 5), account_notes=self.account_notes
        )
        brief = {
            "photo": analysis,
            "caption": caption_text,
            "recent_tag_sets_newest_first": recent_tag_sets,
            "tag_results": tag_stats,
        }
        text = json.dumps(brief, ensure_ascii=False, indent=1)
        if feedback:
            text += f"\n\nThe reviewer's feedback on the previous set:\n{feedback}"
        result = self._ask(system, [{"type": "text", "text": text}], HashtagSet)
        for t in result.hashtags:
            log.info("Hashtag %s (%s): %s", t.tag, t.role, t.reason)
        return [t.tag for t in result.hashtags]

    def review(self, image_jpeg, *, caption):
        text = (
            f"Caption:\n{caption.text}\n\nQuote: {caption.quote or '(none)'}"
            f"{' by ' + caption.quote_author if caption.quote_author else ''}\n\n"
            f"Hashtags: {' '.join(caption.hashtags)}\n\nAlt text: {caption.alt_text}"
        )
        result = self._ask(load_prompt("reviewer"), [image_block(image_jpeg), {"type": "text", "text": text}], ReviewResult)
        return Review(approved=result.verdict == "approve", feedback=result.feedback)
