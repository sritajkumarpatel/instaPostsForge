"""Open models on Ollama Cloud, through Ollama's own chat API with a JSON schema for the reply."""

from __future__ import annotations

import base64
import json

import requests
from pydantic import ValidationError

from .base import Image, ModelClient, ModelReply, Text

BASE_URL = "https://ollama.com"


class OllamaError(RuntimeError):
    def __init__(self, status_code: int, path: str):
        super().__init__(f"Ollama Cloud returned HTTP {status_code} for {path}.")
        self.status_code = status_code


class OllamaClient(ModelClient):
    provider = "ollama"

    def __init__(self, session: requests.Session | None = None, *, api_key: str | None = None, timeout: float = 300):
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"
        self.timeout = timeout

    def _post(self, path: str, body: dict) -> dict:
        response = self.session.post(BASE_URL + path, json=body, timeout=self.timeout)
        if response.status_code >= 400:
            raise OllamaError(response.status_code, path)  # never the response body
        return response.json()

    def check(self, model):
        # A one-token chat: Ollama Cloud has no free call that proves both the key and the model.
        self._post("/api/chat", {"model": model, "messages": [{"role": "user", "content": "ok"}], "stream": False, "options": {"num_predict": 1}})

    def ask(self, *, model, effort, max_tokens, system, parts, schema):
        # Ollama takes a message's images apart from its text, so each Image becomes an image of the
        # user message and the text keeps "Photo n:" labels in order. Effort isn't sent: most open
        # models on Ollama don't think, and asking one that can't is an error. Open models don't
        # always keep to the `format` schema, so it is also spelled out in the system prompt, and a
        # reply that still doesn't fit gets one more try.
        text = "\n\n".join(p.text for p in parts if isinstance(p, Text))
        images = [base64.standard_b64encode(p.jpeg).decode("ascii") for p in parts if isinstance(p, Image)]
        user = {"role": "user", "content": text}
        if images:
            user["images"] = images
        json_schema = schema.model_json_schema()
        system += "\n\nReply with only a JSON object, no other text, that matches this JSON schema:\n" + json.dumps(json_schema)
        messages = [{"role": "system", "content": system}, user]
        tokens = {"input_tokens": 0, "output_tokens": 0}
        for attempt in range(2):
            data = self._post(
                "/api/chat",
                {"model": model, "messages": messages, "format": json_schema, "stream": False, "options": {"num_predict": max_tokens}},
            )
            tokens["input_tokens"] += int(data.get("prompt_eval_count") or 0)
            tokens["output_tokens"] += int(data.get("eval_count") or 0)
            done = data.get("done_reason") or "stop"
            if done == "length":
                return ModelReply(parsed=None, stop="too_long", tokens=tokens, detail="done_reason=length")
            content = (data.get("message") or {}).get("content") or ""
            parsed, problem = parse_reply(schema, content)
            if parsed is not None:
                return ModelReply(parsed=parsed, tokens=tokens, detail=f"done_reason={done}")
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"That reply was not usable ({problem}). Reply again with only the JSON object."},
            ]
        # The workflow treats this as no usable reply. The detail names fields, never the reply's text.
        return ModelReply(parsed=None, tokens=tokens, detail=f"done_reason={done}, {problem}")


def parse_reply(schema, content: str):
    """The reply as `schema`, or None and what was wrong. Code fences and text around the object are ignored."""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        return None, "no JSON object" if content.strip() else "empty reply"
    try:
        return schema.model_validate_json(content[start : end + 1]), ""
    except ValidationError as err:
        fields = sorted({".".join(str(x) for x in e["loc"]) or e["type"] for e in err.errors()})
        return None, "invalid: " + ", ".join(fields[:6])
