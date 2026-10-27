"""The five agents, backed by Claude. Prompts live in instapostsforge/prompts/*.md."""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import date
from pathlib import Path
from importlib import resources
from string import Template
from typing import Literal, TypeVar

import anthropic
from pydantic import BaseModel, Field

from ..models import Caption
from ..usage import UsageLog
from .base import AIWorkflow, PlanItem, Review, SetPhoto
from .playbooks import Playbooks

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

AGENTS = ("analyst", "planner", "caption_writer", "hashtags", "reviewer")

Category = Literal["landscape", "sky", "city", "street", "people", "food", "animals", "macro", "night", "travel", "event", "abstract"]
Mood = Literal["calm", "joyful", "energetic", "nostalgic", "dramatic", "romantic", "playful", "awe"]
CheckName = Literal["accuracy", "hook", "tone", "length", "quote", "hashtags", "alt_text", "safety"]

ACCOUNT_FIELDS = (
    ("niche", "What the account is about"),
    ("audience", "Who follows it"),
    ("location", "Where the photographer is based"),
    ("voice", "How the account sounds"),
    ("avoid", "Never write about or in this way"),
    ("ending_style", "Preferred way to end a caption"),
)

QUOTE_RULES = {
    "always": "Always include one quote that fits the mood of the photo.",
    "when_fitting": "Include a quote only when one genuinely fits the photo; otherwise leave it out.",
    "never": "Do not include a quote.",
}


class PhotoAnalysis(BaseModel):
    category: Category
    mood: Mood
    subject: str = Field(description="The main subject in a few words.")
    main_subject: str = Field(
        description="The main subject as one or two lowercase words, the same wording every time, e.g. moon, dog, sunset, dosa, temple."
    )
    description: str = Field(description="Two or three sentences on what is in the frame.")
    setting: str = Field(description="The kind of place, e.g. hill road, old market, beach at dusk.")
    place_name: str | None = Field(default=None, description="Only when clearly recognisable; never a guess.")
    colors: list[str]
    time_of_day: str | None = None
    season: str | None = None
    quality_score: int = Field(ge=1, le=10)
    has_people: bool
    has_children: bool
    has_identifiable_strangers: bool
    has_text_or_logo: bool
    is_screenshot_or_meme: bool
    shows_private_home: bool
    is_explicit: bool = Field(default=False, description="Nudity, sexual content or graphic gore; such photos are never posted.")
    themes: list[str] = Field(description="Themes and audiences, specific to broad.")


class PlannedPhoto(BaseModel):
    photo_id: str = Field(description="The photo to post, or the cover (first photo) of a multi-photo post.")
    extra_photo_ids: list[str] = Field(
        default_factory=list,
        description="For a multi-photo post only: the other photos, in the order to show them, all from the cover's set. Empty for a single photo.",
    )
    reason: str
    angle: str


class Plan(BaseModel):
    order: list[PlannedPhoto]


class CaptionDraft(BaseModel):
    caption: str = Field(description="Caption text without hashtags; opens with a short hook line.")
    quote: str | None = None
    quote_author: str | None = None
    quote_is_attributed: bool = Field(default=False, description="True only if certain who said the quote.")
    alt_text: str = Field(description="Alt text for the photo, or for the cover of a multi-photo post.")
    more_alt_texts: list[str] = Field(
        default_factory=list, description="Multi-photo posts only: one alt text for each other photo, in order."
    )


class ChosenTag(BaseModel):
    tag: str
    role: Literal["specific", "place", "niche", "community"]
    reason: str


class HashtagSet(BaseModel):
    hashtags: list[ChosenTag]


class ReviewCheck(BaseModel):
    item: CheckName
    passed: bool
    note: str = ""


class ReviewResult(BaseModel):
    checks: list[ReviewCheck]
    verdict: Literal["approve", "revise"]
    feedback: str = Field(default="", description="Names each failed item and what to change.")


def load_prompt(name: str) -> str:
    return resources.files("instapostsforge.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


def image_block(image_jpeg: bytes) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(image_jpeg).decode("ascii")},
    }


class ClaudeWorkflow(AIWorkflow):
    def __init__(self, settings: dict, client: anthropic.Anthropic | None = None, usage_log: UsageLog | None = None):
        self.settings = settings
        self.model = settings.get("model", "claude-haiku-5-5")
        self.client = client or anthropic.Anthropic()
        self.usage_log = usage_log
        self.account_notes = self._account_notes(settings)
        self.standing_instructions = self._load_instructions(settings.get("instructions_file"))
        self.playbooks = Playbooks.load(settings.get("playbooks_dir"))

    @staticmethod
    def _account_notes(settings: dict) -> str:
        account = dict(settings.get("account") or {})
        if settings.get("account_theme") and not account.get("niche"):  # older configs
            account["niche"] = settings["account_theme"]
        lines = [f"- {label}: {account[key]}" for key, label in ACCOUNT_FIELDS if account.get(key)]
        if settings.get("extra_instructions"):
            lines.append(f"- Also keep in mind: {settings['extra_instructions']}")
        if not lines:
            return ""
        return "\n## About this account\n\n" + "\n".join(lines) + "\n"

    def _prompt(self, name: str, **values) -> str:
        values.setdefault("caption_max", int(self.settings.get("max_caption_text_chars", 150)))
        return Template(load_prompt(name)).safe_substitute(account_notes=self.account_notes, **values)

    @staticmethod
    def _load_instructions(path: str | None) -> str:
        if not path:
            return ""
        file = Path(path)
        if not file.exists():
            log.warning("AI instructions file %s not found; continuing without it.", path)
            return ""
        return file.read_text(encoding="utf-8").strip()

    def agent_settings(self, agent: str) -> dict:
        """Model, effort and token limit for one agent: its own entry overrides the ai defaults."""
        own = (self.settings.get("agents") or {}).get(agent) or {}
        return {
            "model": own.get("model", self.model),
            "effort": own.get("effort", self.settings.get("effort")),
            "max_tokens": int(own.get("max_tokens", self.settings.get("max_tokens", 16000))),
        }

    def _ask(self, agent: str, system: str, content: list[dict], schema: type[T]) -> T:
        cfg = self.agent_settings(agent)
        if self.standing_instructions:
            system += "\n\n# Standing instructions from the account owner\n\n" + self.standing_instructions
        kwargs = dict(
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
        )
        if cfg["effort"]:
            kwargs["output_config"] = {"effort": cfg["effort"]}
        started = time.monotonic()
        try:
            response = self.client.messages.parse(**kwargs)
        except Exception as err:
            if self.usage_log:
                self.usage_log.record(agent=agent, model=cfg["model"], seconds=time.monotonic() - started, ok=False, error=str(err))
            raise
        if self.usage_log:
            self.usage_log.record(
                agent=agent,
                model=cfg["model"],
                usage=getattr(response, "usage", None),
                seconds=time.monotonic() - started,
                ok=response.stop_reason not in ("refusal", "max_tokens"),
                error="" if response.stop_reason not in ("refusal", "max_tokens") else f"stop_reason={response.stop_reason}",
            )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"The model declined the {agent} request.")
        if response.parsed_output is None:
            raise RuntimeError(f"No usable {schema.__name__} returned (stop reason: {response.stop_reason}).")
        return response.parsed_output

    def analyze(self, image_jpeg: bytes, *, photo_name: str) -> dict:
        result = self._ask(
            "analyst",
            self._prompt("analyst"),
            [image_block(image_jpeg), {"type": "text", "text": f"File name: {photo_name}\n\nAnalyse this photo."}],
            PhotoAnalysis,
        )
        return result.model_dump()

    def plan(
        self, candidates: list[dict], history: list[dict], *, today: date, horizon: int, photo_sets: dict | None = None
    ) -> list[PlanItem]:
        planning = self.settings.get("planning") or {}
        brief = {
            "today": today.isoformat(),
            "weekday": today.strftime("%A"),
            "plan_this_many_days": horizon,
            "unposted_photos": candidates,
            "recent_posts_newest_first": history,
        }
        if photo_sets:
            brief["photo_sets"] = photo_sets
        else:
            brief["photo_sets"] = "Multi-photo posts are off today: plan single photos only."
        result = self._ask(
            "planner",
            self._prompt(
                "planner",
                max_same_mood=planning.get("max_same_mood_per_7_days", 2),
                subject_days=planning.get("no_repeat_subject_days", 3),
            ),
            [{"type": "text", "text": json.dumps(brief, ensure_ascii=False, indent=1)}],
            Plan,
        )
        return [PlanItem(p.photo_id, p.reason, p.angle, list(p.extra_photo_ids)) for p in result.order]

    @staticmethod
    def _photos_content(image_jpeg: bytes, analysis: dict, more_photos: list[SetPhoto] | None) -> tuple[list[dict], str]:
        """The image blocks and the analyst's notes, for one photo or every photo of a multi-photo post."""
        if not more_photos:
            return [image_block(image_jpeg)], f"Analyst's notes:\n{json.dumps(analysis, ensure_ascii=False)}"
        blocks, notes = [], []
        for n, (jpeg, notes_for) in enumerate([(image_jpeg, analysis), *((p.image_jpeg, p.analysis) for p in more_photos)], 1):
            blocks += [{"type": "text", "text": f"Photo {n}{' (cover)' if n == 1 else ''}:"}, image_block(jpeg)]
            notes.append(f"Photo {n}: {json.dumps(notes_for, ensure_ascii=False)}")
        intro = f"This is a multi-photo post of {len(blocks) // 2} photos, shown in this order; photo 1 is the cover."
        return blocks, intro + "\n\nAnalyst's notes:\n" + "\n".join(notes)

    def write_caption(self, image_jpeg, *, analysis, angle, recent_captions, top_captions, feedback="", more_photos=None):
        system = self._prompt(
            "caption_writer",
            quote_rule=QUOTE_RULES.get(self.settings.get("quote", "when_fitting"), QUOTE_RULES["when_fitting"]),
            language=self.settings.get("language", "English"),
            playbook=self.playbooks.for_photo(analysis),
        )
        blocks, notes = self._photos_content(image_jpeg, analysis, more_photos)
        text = (
            f"{notes}\n\n"
            f"Angle chosen by the planner: {angle or 'your choice'}\n\n"
            f"Recent captions (do not repeat their openings):\n" + ("\n---\n".join(recent_captions) or "(none yet)") + "\n\n"
            "Captions that performed best so far:\n" + ("\n---\n".join(top_captions) or "(no results yet)")
        )
        if feedback:
            text += f"\n\nThe reviewer sent the previous draft back with this feedback:\n{feedback}"
        draft = self._ask("caption_writer", system, [*blocks, {"type": "text", "text": text}], CaptionDraft)
        more_alts = list(draft.more_alt_texts[: len(more_photos or [])])
        more_alts += [""] * (len(more_photos or []) - len(more_alts))  # a missing alt text is left out, never invented
        return Caption(
            text=draft.caption,
            hashtags=[],
            alt_text=draft.alt_text,
            quote=draft.quote,
            quote_author=draft.quote_author if draft.quote_is_attributed else None,
            more_alt_texts=more_alts,
        )

    def choose_hashtags(self, *, analysis, caption_text, recent_tag_sets, tag_stats, feedback="", more_analyses=None):
        tags = self.settings.get("hashtags") or {}
        system = self._prompt(
            "hashtags", min_tags=tags.get("min", 3), max_tags=tags.get("max", 5), playbook=self.playbooks.for_photo(analysis)
        )
        brief = {
            "photo": analysis,
            **({"other_photos_in_this_multi_photo_post": more_analyses} if more_analyses else {}),
            "caption": caption_text,
            "recent_tag_sets_newest_first": recent_tag_sets,
            "tag_results": tag_stats,
        }
        text = json.dumps(brief, ensure_ascii=False, indent=1)
        if feedback:
            text += f"\n\nThe reviewer's feedback on the previous set:\n{feedback}"
        result = self._ask("hashtags", system, [{"type": "text", "text": text}], HashtagSet)
        for t in result.hashtags:
            log.info("Hashtag %s (%s): %s", t.tag, t.role, t.reason)
        return [t.tag for t in result.hashtags]

    def review(self, image_jpeg, *, caption, analysis=None, more_photos=None):
        analysis = analysis or {}
        blocks, notes = self._photos_content(image_jpeg, analysis, more_photos)
        alt = caption.alt_text
        if more_photos:
            alt = "\n".join(f"Photo {n}: {a or '(none)'}" for n, a in enumerate([caption.alt_text, *caption.more_alt_texts], 1))
        text = (
            f"{notes}\n\n"
            f"Caption:\n{caption.text}\n\nQuote: {caption.quote or '(none)'}"
            f"{' by ' + caption.quote_author if caption.quote_author else ''}\n\n"
            f"Hashtags: {' '.join(caption.hashtags)}\n\nAlt text: {alt}"
        )
        system = self._prompt("reviewer", playbook=self.playbooks.for_photo(analysis))
        result = self._ask("reviewer", system, [*blocks, {"type": "text", "text": text}], ReviewResult)
        failed = [c for c in result.checks if not c.passed]
        # A failed checklist item means revise, even if the model's verdict said approve.
        if result.verdict == "approve" and not failed:
            return Review(approved=True)
        feedback = result.feedback or "; ".join(f"{c.item}: {c.note or 'failed'}" for c in failed)
        return Review(approved=False, feedback=feedback or "The reviewer asked for a revision.", failed=[c.item for c in failed])
