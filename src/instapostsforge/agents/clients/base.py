"""What the workflow needs from a model provider: one structured-output call with text and photos."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, TypeVar, Union

from pydantic import BaseModel

from ...usage import TOKEN_FIELDS  # each client reports tokens in the usage log's terms; output includes thinking

T = TypeVar("T", bound=BaseModel)


@dataclass
class Text:
    text: str


@dataclass
class Image:
    jpeg: bytes


Part = Union[Text, Image]


@dataclass
class ModelReply:
    parsed: BaseModel | None
    stop: Literal["done", "refused", "too_long"] = "done"
    tokens: dict[str, int] = field(default_factory=dict)
    detail: str = ""  # the provider's own stop reason, for the usage log


class ModelClient(ABC):
    provider: str

    @abstractmethod
    def ask(
        self, *, model: str, effort: str | None, max_tokens: int, system: str, parts: list[Part], schema: type[T]
    ) -> ModelReply:
        """One request; the reply is parsed into schema, or parsed is None."""

    def check(self, model: str) -> None:
        """Confirms the key works and the model exists, without spending tokens. Raises on failure."""
