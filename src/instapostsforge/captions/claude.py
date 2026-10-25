"""Caption writer backed by Claude's vision model."""

from __future__ import annotations

import base64

import anthropic
from pydantic import BaseModel, Field

from ..models import Caption
from .base import CaptionWriter


class CaptionDraft(BaseModel):
    caption: str = Field(description="The caption text without hashtags. Opens with a short hook line.")
    quote: str | None = Field(default=None, description="A quote that fits the photo, or null.")
    quote_author: str | None = Field(default=None, description="Who said the quote, or null if original.")
    hashtags: list[str] = Field(description="Hashtags for this photo, each starting with #.")
    alt_text: str = Field(description="A plain description of the photo for screen readers.")


SYSTEM_PROMPT = """You write Instagram captions for a photographer's feed. You are shown one photo at a time.

Write for the person scrolling past: the first line is a short hook that makes them stop and read, because only about the first 125 characters show before "more". Match the tone to the photo itself: calm photos get calm words, energetic photos get energetic words. Keep the whole caption short, usually one to three sentences, and end with a light question or prompt that invites comments when it fits naturally. Mention concrete things in the photo (subject, place, light, season) so Instagram search can match it.

Quotes: {quote_rule} Prefer an original line. If you use a known quote, give its real author and only use one you are certain of; never invent or guess an attribution. Do not use song lyrics.

Hashtags: give between {min_tags} and {max_tags}. Choose specific ones for this photo's subject, place and style plus one for the wider niche. Never use generic tags such as #explore, #viral, #instagood or #fyp. Do not put hashtags inside the caption text.

Alt text: one or two plain sentences describing what is in the photo.

Write everything in {language}. Each caption must be fresh: do not reuse the openings or phrases from the recent captions you are shown."""

QUOTE_RULES = {
    "always": "Always include one quote that fits the mood of the photo.",
    "when_fitting": "Include a quote only when one genuinely fits the photo; otherwise leave it out.",
    "never": "Do not include a quote.",
}


class ClaudeCaptionWriter(CaptionWriter):
    def __init__(self, settings: dict, client: anthropic.Anthropic | None = None):
        self.settings = settings
        self.model = settings.get("model", "claude-opus-5-5")
        self.client = client or anthropic.Anthropic()

    def _system_prompt(self) -> str:
        tags = self.settings.get("hashtags") or {}
        prompt = SYSTEM_PROMPT.format(
            quote_rule=QUOTE_RULES.get(self.settings.get("quote", "when_fitting"), QUOTE_RULES["when_fitting"]),
            min_tags=tags.get("min", 3),
            max_tags=tags.get("max", 5),
            language=self.settings.get("language", "English"),
        )
        if self.settings.get("account_theme"):
            prompt += f"\n\nThe account's overall theme: {self.settings['account_theme']}"
        if self.settings.get("extra_instructions"):
            prompt += f"\n\nAdditional instructions from the account owner: {self.settings['extra_instructions']}"
        return prompt

    def write(self, image_jpeg: bytes, *, photo_name: str, recent_captions: list[str]) -> Caption:
        recent = "\n---\n".join(recent_captions[-10:]) or "(none yet)"
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=self._system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64.standard_b64encode(image_jpeg).decode("ascii"),
                            },
                        },
                        {
                            "type": "text",
                            "text": f"File name: {photo_name}\n\nRecent captions on this account:\n{recent}\n\n"
                            "Write the caption for this photo.",
                        },
                    ],
                }
            ],
            output_format=CaptionDraft,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"The caption model declined this photo ({photo_name}).")
        draft = response.parsed_output
        if draft is None:
            raise RuntimeError(f"The caption model returned no usable caption (stop reason: {response.stop_reason}).")
        return Caption(
            text=draft.caption,
            hashtags=draft.hashtags,
            alt_text=draft.alt_text,
            quote=draft.quote,
            quote_author=draft.quote_author,
        )
