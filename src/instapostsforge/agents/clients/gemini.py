"""Gemini models, through Google's official Gen AI SDK (JSON output checked against the schema)."""

from __future__ import annotations

from google import genai
from google.genai import types
from pydantic import ValidationError

from .base import Image, ModelClient, ModelReply, Part

# Our effort words onto Gemini's thinking levels.
THINKING = {"none": "MINIMAL", "minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH", "xhigh": "HIGH", "max": "HIGH"}
BLOCKED = {
    "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION",
}


def to_part(part: Part) -> types.Part:
    if isinstance(part, Image):
        return types.Part.from_bytes(data=part.jpeg, mime_type="image/jpeg")
    return types.Part.from_text(text=part.text)


def _name(value) -> str:
    return str(getattr(value, "name", value) or "")


class GeminiClient(ModelClient):
    provider = "gemini"

    def __init__(self, client: genai.Client | None = None, *, api_key: str | None = None):
        self.client = client or genai.Client(api_key=api_key)

    def check(self, model):
        self.client.models.get(model=model)

    def ask(self, *, model, effort, max_tokens, system, parts, schema):
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
        )
        if effort in THINKING:
            config.thinking_config = types.ThinkingConfig(thinking_level=THINKING[effort])
        response = self.client.models.generate_content(
            model=model, contents=[types.Content(role="user", parts=[to_part(p) for p in parts])], config=config
        )
        usage = getattr(response, "usage_metadata", None)
        cached = int(getattr(usage, "cached_content_token_count", 0) or 0)
        tokens = {
            "input_tokens": int(getattr(usage, "prompt_token_count", 0) or 0) - cached,
            "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0) + int(getattr(usage, "thoughts_token_count", 0) or 0),
            "cache_read_input_tokens": cached,
        }
        block = _name(getattr(getattr(response, "prompt_feedback", None), "block_reason", None))
        if block and block != "BLOCKED_REASON_UNSPECIFIED":
            return ModelReply(parsed=None, stop="refused", tokens=tokens, detail=f"prompt blocked: {block}")
        candidates = getattr(response, "candidates", None) or []
        finish = _name(getattr(candidates[0], "finish_reason", None)) if candidates else "NO_CANDIDATES"
        if finish in BLOCKED:
            return ModelReply(parsed=None, stop="refused", tokens=tokens, detail=f"finish_reason={finish}")
        if finish == "MAX_TOKENS":
            return ModelReply(parsed=None, stop="too_long", tokens=tokens, detail="finish_reason=MAX_TOKENS")
        try:
            parsed = schema.model_validate_json(response.text or "")
        except ValidationError:
            parsed = None  # the workflow treats this as no usable reply and falls back
        return ModelReply(parsed=parsed, tokens=tokens, detail=f"finish_reason={finish}")
