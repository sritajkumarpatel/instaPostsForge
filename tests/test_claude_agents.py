"""Checks the Claude agents build sensible requests, using a fake client (no API calls)."""

from datetime import date
from types import SimpleNamespace

from instapostsforge.agents.claude import (
    CaptionDraft,
    ChosenTag,
    ClaudeWorkflow,
    HashtagSet,
    Plan,
    PlannedPhoto,
    ReviewResult,
    load_prompt,
)
from instapostsforge.models import Caption


class FakeMessages:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=500)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=output, usage=usage)


def workflow(*outputs, usage_log=None, **settings):
    messages = FakeMessages(outputs)
    return ClaudeWorkflow(settings, client=SimpleNamespace(messages=messages), usage_log=usage_log), messages


def test_every_prompt_file_loads():
    for name in ("analyst", "planner", "caption_writer", "hashtags", "reviewer"):
        assert len(load_prompt(name)) > 100


def test_caption_drops_unverified_quote_author():
    wf, msgs = workflow(CaptionDraft(caption="Hi", quote="Be still.", quote_author="Someone", quote_is_attributed=False, alt_text="x"))
    caption = wf.write_caption(b"jpg", analysis={}, angle="calm", recent_captions=[], top_captions=[])
    assert caption.quote == "Be still." and caption.quote_author is None
    system = msgs.calls[0]["system"]
    assert "English" in system and "$" not in system
    assert msgs.calls[0]["model"] == "claude-opus-5-5"


def test_planner_and_hashtags_and_review_round_trip():
    wf, msgs = workflow(
        Plan(order=[PlannedPhoto(photo_id="a", reason="r", angle="g")]),
        HashtagSet(hashtags=[ChosenTag(tag="#rain", role="specific", reason="r")]),
        ReviewResult(verdict="revise", feedback="Shorter hook"),
        account_theme="street",
    )
    assert wf.plan([{"photo_id": "a"}], [], today=date(2026, 10, 25), horizon=1)[0].photo_id == "a"
    assert wf.choose_hashtags(analysis={}, caption_text="c", recent_tag_sets=[], tag_stats={}) == ["#rain"]
    review = wf.review(b"jpg", caption=Caption(text="c", hashtags=["#rain"], alt_text="a"))
    assert not review.approved and review.feedback == "Shorter hook"
    assert "street" in msgs.calls[1]["system"]


def test_per_agent_model_effort_and_tokens():
    settings = {
        "model": "claude-opus-5-5",
        "effort": "medium",
        "max_tokens": 16000,
        "agents": {"hashtags": {"model": "claude-sonnet-5-5", "effort": "low", "max_tokens": 4000}},
    }
    wf, msgs = workflow(
        HashtagSet(hashtags=[ChosenTag(tag="#rain", role="specific", reason="r")]),
        ReviewResult(verdict="approve"),
        **settings,
    )
    wf.choose_hashtags(analysis={}, caption_text="c", recent_tag_sets=[], tag_stats={})
    wf.review(b"jpg", caption=Caption(text="c", hashtags=["#rain"], alt_text="a"))
    tags_call, review_call = msgs.calls
    assert (tags_call["model"], tags_call["max_tokens"], tags_call["output_config"]) == ("claude-sonnet-5-5", 4000, {"effort": "low"})
    assert (review_call["model"], review_call["max_tokens"], review_call["output_config"]) == ("claude-opus-5-5", 16000, {"effort": "medium"})


def test_standing_instructions_reach_every_agent(tmp_path):
    rules = tmp_path / "ai.md"
    rules.write_text("Never mention the weather.")
    wf, msgs = workflow(ReviewResult(verdict="approve"), instructions_file=str(rules))
    wf.review(b"jpg", caption=Caption(text="c", hashtags=["#a"], alt_text="a"))
    assert "Never mention the weather." in msgs.calls[0]["system"]


def test_every_call_is_written_to_the_usage_log(tmp_path):
    from instapostsforge.usage import UsageLog, load_month

    log = UsageLog("p", pricing={"claude-opus-5-5": {"input": 4, "output": 20}}, command="post", state_dir=tmp_path)
    wf, _ = workflow(ReviewResult(verdict="approve"), RuntimeError("overloaded"), usage_log=log)
    wf.review(b"jpg", caption=Caption(text="c", hashtags=["#a"], alt_text="a"))
    try:
        wf.review(b"jpg", caption=Caption(text="c", hashtags=["#a"], alt_text="a"))
    except RuntimeError:
        pass
    ok, failed = log.entries
    assert ok["agent"] == "reviewer" and ok["input_tokens"] == 1000 and ok["cache_read_input_tokens"] == 500
    assert ok["estimated_usd"] == round(1000 * 4e-6 + 500 * 4e-7 + 200 * 20e-6, 6)
    assert failed["ok"] is False and "overloaded" in failed["error"]
    month = ok["at"][:7]
    assert len(load_month("p", month, state_dir=tmp_path)) == 2
    assert log.summary()["total"]["calls"] == 2
