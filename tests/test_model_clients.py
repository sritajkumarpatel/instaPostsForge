"""Each provider's client turns one neutral request into that SDK's call, with fake SDK objects."""

from types import SimpleNamespace

from instapostsforge.agents.clients import Image, Text
from instapostsforge.agents.clients.anthropic import AnthropicClient
from instapostsforge.agents.schemas import HashtagSet


class FakeAnthropicMessages:
    def __init__(self, stop_reason="end_turn", parsed=None):
        self.stop_reason, self.parsed, self.calls = stop_reason, parsed, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=30, cache_read_input_tokens=500)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.parsed, usage=usage)


def ask(client, effort="low"):
    return client.ask(
        model="claude-haiku-5-5", effort=effort, max_tokens=4000, system="sys",
        parts=[Text("Photo 1:"), Image(b"\xff\xd8jpeg")], schema=HashtagSet,
    )


def test_anthropic_request_and_usage():
    messages = FakeAnthropicMessages(parsed=HashtagSet(hashtags=[]))
    reply = ask(AnthropicClient(SimpleNamespace(messages=messages)))
    call = messages.calls[0]
    assert (call["model"], call["max_tokens"], call["system"], call["output_format"]) == ("claude-haiku-5-5", 4000, "sys", HashtagSet)
    assert call["output_config"] == {"effort": "low"}
    text, image = call["messages"][0]["content"]
    assert text == {"type": "text", "text": "Photo 1:"}
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    assert reply.stop == "done" and reply.parsed == HashtagSet(hashtags=[])
    assert reply.tokens == {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 30, "cache_read_input_tokens": 500}


def test_anthropic_without_effort_sends_no_output_config():
    messages = FakeAnthropicMessages(parsed=HashtagSet(hashtags=[]))
    ask(AnthropicClient(SimpleNamespace(messages=messages)), effort=None)
    assert "output_config" not in messages.calls[0]


def test_anthropic_stop_reasons_are_normalised():
    for stop_reason, stop in (("refusal", "refused"), ("max_tokens", "too_long"), ("end_turn", "done")):
        reply = ask(AnthropicClient(SimpleNamespace(messages=FakeAnthropicMessages(stop_reason))))
        assert reply.stop == stop and reply.detail == f"stop_reason={stop_reason}"
