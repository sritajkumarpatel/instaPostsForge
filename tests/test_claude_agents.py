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
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.outputs.pop(0))


def workflow(*outputs, **settings):
    messages = FakeMessages(outputs)
    return ClaudeWorkflow(settings, client=SimpleNamespace(messages=messages)), messages


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
