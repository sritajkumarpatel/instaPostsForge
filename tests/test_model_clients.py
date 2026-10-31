"""Each provider's client turns one neutral request into that SDK's call, with fake SDK objects."""

import base64
import json
from types import SimpleNamespace

from instapostsforge.agents.clients import Image, Text
from instapostsforge.agents.clients.anthropic import AnthropicClient
from instapostsforge.agents.clients.gemini import GeminiClient
from instapostsforge.agents.clients.ollama import OllamaClient, OllamaError
from instapostsforge.agents.clients.openai import OpenAIClient
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


# --- OpenAI ---


class FakeResponses:
    def __init__(self, response):
        self.response, self.calls = response, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def openai_response(parsed=None, status="completed", reason=None, refusal=False):
    usage = SimpleNamespace(
        input_tokens=1500, output_tokens=300,
        input_tokens_details=SimpleNamespace(cached_tokens=500), output_tokens_details=SimpleNamespace(reasoning_tokens=100),
    )
    content = [SimpleNamespace(type="refusal", refusal="no")] if refusal else [SimpleNamespace(type="output_text", text="{}")]
    return SimpleNamespace(
        output=[SimpleNamespace(type="message", content=content)], usage=usage, status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None, output_parsed=parsed,
    )


def openai_ask(response, effort="low"):
    responses = FakeResponses(response)
    reply = OpenAIClient(SimpleNamespace(responses=responses)).ask(
        model="gpt-5.6-luna", effort=effort, max_tokens=4000, system="sys",
        parts=[Text("Photo 1:"), Image(b"\xff\xd8jpeg")], schema=HashtagSet,
    )
    return reply, responses.calls[0]


def test_openai_request_and_usage():
    reply, call = openai_ask(openai_response(parsed=HashtagSet(hashtags=[])))
    assert (call["model"], call["instructions"], call["text_format"], call["max_output_tokens"]) == ("gpt-5.6-luna", "sys", HashtagSet, 4000)
    assert call["reasoning"] == {"effort": "low"} and call["store"] is False
    text, image = call["input"][0]["content"]
    assert text == {"type": "input_text", "text": "Photo 1:"}
    assert image["type"] == "input_image" and image["image_url"].startswith("data:image/jpeg;base64,")
    assert reply.stop == "done" and reply.parsed == HashtagSet(hashtags=[])
    # cached input is billed apart; reasoning is already inside output_tokens
    assert reply.tokens == {"input_tokens": 1000, "output_tokens": 300, "cache_read_input_tokens": 500}


def test_openai_refusals_and_truncation():
    assert openai_ask(openai_response(refusal=True))[0].stop == "refused"
    assert openai_ask(openai_response(status="incomplete", reason="content_filter"))[0].stop == "refused"
    reply = openai_ask(openai_response(status="incomplete", reason="max_output_tokens"))[0]
    assert reply.stop == "too_long" and reply.parsed is None


def test_openai_unknown_effort_is_left_out():
    assert "reasoning" not in openai_ask(openai_response(parsed=HashtagSet(hashtags=[])), effort=None)[1]


# --- Gemini ---


class FakeGeminiModels:
    def __init__(self, response):
        self.response, self.calls = response, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def gemini_response(text='{"hashtags": []}', finish="STOP", block=None):
    usage = SimpleNamespace(prompt_token_count=1500, cached_content_token_count=500, candidates_token_count=200, thoughts_token_count=100)
    return SimpleNamespace(
        text=text, usage_metadata=usage, prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
    )


def gemini_ask(response, effort="medium"):
    models = FakeGeminiModels(response)
    reply = GeminiClient(SimpleNamespace(models=models)).ask(
        model="gemini-3.1-flash-lite", effort=effort, max_tokens=4000, system="sys",
        parts=[Text("Photo 1:"), Image(b"\xff\xd8jpeg")], schema=HashtagSet,
    )
    return reply, models.calls[0]


def test_gemini_request_and_usage():
    reply, call = gemini_ask(gemini_response())
    config = call["config"]
    assert call["model"] == "gemini-3.1-flash-lite"
    assert config.system_instruction == "sys" and config.max_output_tokens == 4000 and config.response_mime_type == "application/json"
    assert config.response_json_schema == HashtagSet.model_json_schema()
    assert config.thinking_config.thinking_level.name == "MEDIUM"
    text, image = call["contents"][0].parts
    assert text.text == "Photo 1:" and image.inline_data.mime_type == "image/jpeg" and image.inline_data.data == b"\xff\xd8jpeg"
    assert reply.stop == "done" and reply.parsed == HashtagSet(hashtags=[])
    # thinking is billed as output; cached input apart
    assert reply.tokens == {"input_tokens": 1000, "output_tokens": 300, "cache_read_input_tokens": 500}


def test_gemini_effort_tops_out_at_high():
    assert gemini_ask(gemini_response(), effort="max")[1]["config"].thinking_config.thinking_level.name == "HIGH"
    assert gemini_ask(gemini_response(), effort=None)[1]["config"].thinking_config is None


def test_gemini_blocks_truncation_and_bad_json():
    assert gemini_ask(gemini_response(finish="SAFETY"))[0].stop == "refused"
    assert gemini_ask(gemini_response(block=SimpleNamespace(name="PROHIBITED_CONTENT")))[0].stop == "refused"
    assert gemini_ask(gemini_response(finish="MAX_TOKENS"))[0].stop == "too_long"
    reply = gemini_ask(gemini_response(text='{"hashtags": "not a list"}'))[0]
    assert reply.stop == "done" and reply.parsed is None  # the workflow then reports no usable reply


def test_every_schema_converts_for_openai_strict_mode_and_gemini():
    import json

    from openai.lib._pydantic import to_strict_json_schema

    from instapostsforge.agents import schemas

    for schema in (schemas.PhotoAnalysis, schemas.Plan, schemas.CaptionDraft, schemas.HashtagSet, schemas.ReviewResult):
        strict = to_strict_json_schema(schema)
        assert strict["additionalProperties"] is False and set(strict["required"]) == set(strict["properties"])
        json.dumps(schema.model_json_schema())


# --- Ollama Cloud ---


class FakeSession:
    def __init__(self, *payloads, status=200):
        self.payloads, self.status, self.calls, self.headers = list(payloads), status, [], {}

    def post(self, url, json, timeout):
        self.calls.append((url, json))
        payload = self.payloads[min(len(self.calls), len(self.payloads)) - 1]
        return SimpleNamespace(status_code=self.status, json=lambda: payload, text="secret body")


def ollama_ask(*payloads, status=200):
    session = FakeSession(*payloads, status=status)
    client = OllamaClient(session, api_key="ok-key")
    reply = client.ask(
        model="gemma4:31b", effort="medium", max_tokens=4000, system="sys",
        parts=[Text("Photo 1:"), Image(b"\xff\xd8one"), Text("Photo 2:"), Image(b"\xff\xd8two"), Text("Notes")], schema=HashtagSet,
    )
    return reply, session


def test_ollama_request_and_usage():
    reply, session = ollama_ask({"message": {"content": '{"hashtags": []}'}, "done_reason": "stop", "prompt_eval_count": 900, "eval_count": 120})
    url, body = session.calls[0]
    assert url == "https://ollama.com/api/chat" and session.headers["Authorization"] == "Bearer ok-key"
    assert body["model"] == "gemma4:31b" and body["stream"] is False and body["options"] == {"num_predict": 4000}
    assert body["format"] == HashtagSet.model_json_schema() and "think" not in body
    system, user = body["messages"]
    assert system["role"] == "system" and system["content"].startswith("sys\n\nReply with only a JSON object")
    assert json.dumps(HashtagSet.model_json_schema()) in system["content"]
    assert user["content"] == "Photo 1:\n\nPhoto 2:\n\nNotes"
    assert [base64.b64decode(i) for i in user["images"]] == [b"\xff\xd8one", b"\xff\xd8two"]  # in order
    assert reply.stop == "done" and reply.parsed == HashtagSet(hashtags=[])
    assert reply.tokens == {"input_tokens": 900, "output_tokens": 120}


def test_ollama_truncation_and_bad_json():
    assert ollama_ask({"message": {"content": '{"hasht'}, "done_reason": "length"})[0].stop == "too_long"
    reply, session = ollama_ask({"message": {"content": "not json"}, "done_reason": "stop", "eval_count": 5})
    assert reply.stop == "done" and reply.parsed is None and len(session.calls) == 2  # one more try
    assert reply.tokens["output_tokens"] == 10 and "no JSON object" in reply.detail


def test_ollama_reads_json_in_a_code_fence():
    reply = ollama_ask({"message": {"content": 'Here it is:\n```json\n{"hashtags": []}\n```'}, "done_reason": "stop"})[0]
    assert reply.parsed == HashtagSet(hashtags=[])


def test_ollama_retries_once_and_names_the_bad_fields():
    bad = {"message": {"content": '{"hashtags": [{"tag": "#a", "role": "other", "reason": "x"}]}'}, "done_reason": "stop"}
    good = {"message": {"content": '{"hashtags": []}'}, "done_reason": "stop"}
    reply, session = ollama_ask(bad, good)
    assert reply.parsed == HashtagSet(hashtags=[]) and len(session.calls) == 2
    retry = session.calls[1][1]["messages"]
    assert retry[-2]["role"] == "assistant" and "hashtags.0.role" in retry[-1]["content"]

    reply = ollama_ask(bad)[0]
    assert reply.parsed is None and "hashtags.0.role" in reply.detail and "#a" not in reply.detail


def test_ollama_error_hides_the_body():
    import pytest

    with pytest.raises(OllamaError) as err:
        ollama_ask({}, status=401)
    assert err.value.status_code == 401 and "secret body" not in str(err.value)


def test_ollama_check_is_a_one_token_call():
    session = FakeSession({"message": {"content": "o"}})
    OllamaClient(session, api_key="k").check("gemma4:31b")
    url, body = session.calls[0]
    assert url.endswith("/api/chat") and body["model"] == "gemma4:31b" and body["options"] == {"num_predict": 1}
