"""Open models on Ollama Cloud, through Ollama's own chat API with a JSON schema for the reply."""

from __future__ import annotations

import base64

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
        # models on Ollama don't think, and asking one that can't is an error.
        text = "\n\n".join(p.text for p in parts if isinstance(p, Text))
        images = [base64.standard_b64encode(p.jpeg).decode("ascii") for p in parts if isinstance(p, Image)]
        user = {"role": "user", "content": text}
        if images:
            user["images"] = images
        data = self._post(
            "/api/chat",
            {
                "model": model,
                "messages": [{"role": "system", "content": system}, user],
                "format": schema.model_json_schema(),
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
        )
        tokens = {"input_tokens": int(data.get("prompt_eval_count") or 0), "output_tokens": int(data.get("eval_count") or 0)}
        done = data.get("done_reason") or "stop"
        if done == "length":
            return ModelReply(parsed=None, stop="too_long", tokens=tokens, detail="done_reason=length")
        try:
            parsed = schema.model_validate_json((data.get("message") or {}).get("content") or "")
        except ValidationError:
            parsed = None  # the workflow treats this as no usable reply and falls back
        return ModelReply(parsed=parsed, tokens=tokens, detail=f"done_reason={done}")
