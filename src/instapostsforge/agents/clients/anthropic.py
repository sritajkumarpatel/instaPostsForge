"""Claude, through the official Anthropic SDK."""

from __future__ import annotations

import base64

import anthropic

from .base import TOKEN_FIELDS, Image, ModelClient, ModelReply, Part


def content_block(part: Part) -> dict:
    if isinstance(part, Image):
        data = base64.standard_b64encode(part.jpeg).decode("ascii")
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
    return {"type": "text", "text": part.text}


class AnthropicClient(ModelClient):
    provider = "claude"
    key_env = "ANTHROPIC_API_KEY"

    def __init__(self, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()

    def ask(self, *, model, effort, max_tokens, system, parts, schema):
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": [content_block(p) for p in parts]}],
            output_format=schema,
        )
        if effort:
            kwargs["output_config"] = {"effort": effort}
        response = self.client.messages.parse(**kwargs)
        usage = getattr(response, "usage", None)
        stop = {"refusal": "refused", "max_tokens": "too_long"}.get(response.stop_reason, "done")
        return ModelReply(
            parsed=response.parsed_output,
            stop=stop,
            tokens={f: int(getattr(usage, f, 0) or 0) for f in TOKEN_FIELDS},
            detail=f"stop_reason={response.stop_reason}",
        )
