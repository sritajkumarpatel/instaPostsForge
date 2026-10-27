"""OpenAI models, through the official OpenAI SDK (Responses API, strict structured output)."""

from __future__ import annotations

import base64

import openai

from .base import Image, ModelClient, ModelReply, Part

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def content_item(part: Part) -> dict:
    if isinstance(part, Image):
        data = base64.standard_b64encode(part.jpeg).decode("ascii")
        return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{data}", "detail": "auto"}
    return {"type": "input_text", "text": part.text}


def refused(response) -> bool:
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", "") == "refusal":
                return True
    return False


class OpenAIClient(ModelClient):
    provider = "openai"

    def __init__(self, client: openai.OpenAI | None = None, *, api_key: str | None = None):
        self.client = client or openai.OpenAI(api_key=api_key)

    def check(self, model):
        self.client.models.retrieve(model)

    def ask(self, *, model, effort, max_tokens, system, parts, schema):
        kwargs = dict(
            model=model,
            instructions=system,
            input=[{"role": "user", "content": [content_item(p) for p in parts]}],
            text_format=schema,
            max_output_tokens=max_tokens,
            store=False,
        )
        if effort in EFFORTS:
            kwargs["reasoning"] = {"effort": effort}
        response = self.client.responses.parse(**kwargs)
        usage = getattr(response, "usage", None)
        cached = int(getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0)
        tokens = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0) - cached,
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),  # includes reasoning
            "cache_read_input_tokens": cached,
        }
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if refused(response) or reason == "content_filter":
            return ModelReply(parsed=None, stop="refused", tokens=tokens, detail=f"refusal ({reason or 'model'})")
        if getattr(response, "status", "") == "incomplete":
            stop = "too_long" if reason == "max_output_tokens" else "refused"
            return ModelReply(parsed=None, stop=stop, tokens=tokens, detail=f"incomplete: {reason}")
        return ModelReply(parsed=response.output_parsed, tokens=tokens, detail=f"status={response.status}")
