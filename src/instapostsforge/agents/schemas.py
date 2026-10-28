"""Structured-output schemas for the five agents. Every provider's reply is validated against these."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["landscape", "sky", "city", "street", "people", "food", "animals", "macro", "night", "travel", "event", "abstract"]
Mood = Literal["calm", "joyful", "energetic", "nostalgic", "dramatic", "romantic", "playful", "awe"]
CheckName = Literal["accuracy", "hook", "tone", "length", "quote", "hashtags", "alt_text", "safety"]


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
    # Where the main subject sits, so a trim to Instagram's shape can leave it whole. Code checks the
    # numbers (imaging.focus_from); no range limits here, so one odd number can't sink the whole analysis.
    subject_top: float | None = Field(default=None, description="Where the main subject starts, as a fraction of the height: 0 is the top edge.")
    subject_bottom: float | None = Field(default=None, description="Where the main subject ends, as a fraction of the height: 1 is the bottom edge.")
    subject_left: float | None = Field(default=None, description="Where the main subject starts, as a fraction of the width: 0 is the left edge.")
    subject_right: float | None = Field(default=None, description="Where the main subject ends, as a fraction of the width: 1 is the right edge.")


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
