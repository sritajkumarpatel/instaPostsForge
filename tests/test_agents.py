"""Checks the agents build sensible requests, using a fake model client (no API calls)."""

from datetime import date

from instapostsforge.agents.clients import Image, ModelClient, ModelReply, Text
from instapostsforge.agents.schemas import CaptionDraft, ChosenTag, HashtagSet, Plan, PlannedPhoto, ReviewResult
from instapostsforge.agents.workflow import AgentWorkflow, load_prompt
from instapostsforge.models import Caption


class FakeClient(ModelClient):
    """Returns canned replies in order and keeps every request."""

    provider = "fake"
    key_env = "FAKE_API_KEY"

    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    def ask(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        if isinstance(output, ModelReply):
            return output
        tokens = {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500}
        return ModelReply(parsed=output, tokens=tokens)


def workflow(*outputs, usage_log=None, **settings):
    client = FakeClient(outputs)
    return AgentWorkflow(settings, client=client, usage_log=usage_log), client


def test_every_prompt_file_loads():
    for name in ("analyst", "planner", "caption_writer", "hashtags", "reviewer"):
        assert len(load_prompt(name)) > 100


def test_caption_drops_unverified_quote_author():
    wf, msgs = workflow(CaptionDraft(caption="Hi", quote="Be still.", quote_author="Someone", quote_is_attributed=False, alt_text="x"))
    caption = wf.write_caption(b"jpg", analysis={}, angle="calm", recent_captions=[], top_captions=[])
    assert caption.quote == "Be still." and caption.quote_author is None
    system = msgs.calls[0]["system"]
    assert "English" in system and "$" not in system
    assert msgs.calls[0]["model"] == "claude-haiku-5-5"


def test_planner_and_hashtags_and_review_round_trip():
    wf, msgs = workflow(
        Plan(order=[PlannedPhoto(photo_id="a", reason="r", angle="g")]),
        HashtagSet(hashtags=[ChosenTag(tag="#rain", role="specific", reason="r")]),
        ReviewResult(checks=[], verdict="revise", feedback="Shorter hook"),
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
        ReviewResult(checks=[], verdict="approve"),
        **settings,
    )
    wf.choose_hashtags(analysis={}, caption_text="c", recent_tag_sets=[], tag_stats={})
    wf.review(b"jpg", caption=Caption(text="c", hashtags=["#rain"], alt_text="a"))
    tags_call, review_call = msgs.calls
    assert (tags_call["model"], tags_call["max_tokens"], tags_call["effort"]) == ("claude-sonnet-5-5", 4000, "low")
    assert (review_call["model"], review_call["max_tokens"], review_call["effort"]) == ("claude-opus-5-5", 16000, "medium")


def test_standing_instructions_reach_every_agent(tmp_path):
    rules = tmp_path / "ai.md"
    rules.write_text("Never mention the weather.")
    wf, msgs = workflow(ReviewResult(checks=[], verdict="approve"), instructions_file=str(rules))
    wf.review(b"jpg", caption=Caption(text="c", hashtags=["#a"], alt_text="a"))
    assert "Never mention the weather." in msgs.calls[0]["system"]


def test_every_call_is_written_to_the_usage_log(tmp_path):
    from instapostsforge.usage import UsageLog, load_month

    log = UsageLog("p", pricing={"claude-opus-5-5": {"input": 4, "output": 20}}, command="post", state_dir=tmp_path)
    wf, _ = workflow(ReviewResult(checks=[], verdict="approve"), RuntimeError("overloaded"), usage_log=log, model="claude-opus-5-5")
    wf.review(b"jpg", caption=Caption(text="c", hashtags=["#a"], alt_text="a"))
    try:
        wf.review(b"jpg", caption=Caption(text="c", hashtags=["#a"], alt_text="a"))
    except RuntimeError:
        pass
    ok, failed = log.entries
    assert ok["agent"] == "reviewer" and ok["provider"] == "fake" and ok["input_tokens"] == 1000 and ok["cache_read_input_tokens"] == 500
    assert ok["estimated_usd"] == round(1000 * 4e-6 + 500 * 4e-7 + 200 * 20e-6, 6)
    assert failed["ok"] is False and "overloaded" in failed["error"]
    month = ok["at"][:7]
    assert len(load_month("p", month, state_dir=tmp_path)) == 2
    assert log.summary()["total"]["calls"] == 2


def test_categories_and_moods_match_the_schema_and_playbooks():
    from pathlib import Path
    from typing import get_args

    from instapostsforge.agents.schemas import Category, Mood
    from instapostsforge.agents.playbooks import CATEGORIES, MOODS, Playbooks

    assert get_args(Category) == CATEGORIES and get_args(Mood) == MOODS
    books = Playbooks.load(Path(__file__).parent.parent / "config" / "playbooks")
    assert set(books.categories) == set(CATEGORIES) and set(books.moods) == set(MOODS)


def test_only_the_photos_playbook_reaches_the_writer(tmp_path):
    (tmp_path / "categories.md").write_text("## food\n\nTaste first.\n\n## sky\n\nColour first.\n")
    (tmp_path / "moods.md").write_text("## playful\n\nA wink of humour.\n")
    wf, msgs = workflow(CaptionDraft(caption="Hi", alt_text="x"), playbooks_dir=str(tmp_path))
    wf.write_caption(b"jpg", analysis={"category": "food", "mood": "playful"}, angle="", recent_captions=[], top_captions=[])
    system = msgs.calls[0]["system"]
    assert "Taste first." in system and "A wink of humour." in system and "Colour first." not in system


def test_failed_check_means_revise_even_if_verdict_approves():
    from instapostsforge.agents.schemas import ReviewCheck

    wf, _ = workflow(ReviewResult(checks=[ReviewCheck(item="hook", passed=False, note="112 characters")], verdict="approve"))
    review = wf.review(b"jpg", caption=Caption(text="c", hashtags=["#a"], alt_text="a"))
    assert not review.approved and review.feedback == "hook: 112 characters"


def test_account_profile_and_rules_fill_every_prompt():
    account = {"niche": "street photography in Pune", "avoid": "the word wanderlust"}
    wf, msgs = workflow(
        Plan(order=[]), ReviewResult(checks=[], verdict="approve"), account=account, planning={"max_same_mood_per_7_days": 3}
    )
    wf.plan([], [], today=date(2026, 10, 25), horizon=1)
    wf.review(b"jpg", caption=Caption(text="c", hashtags=["#a"], alt_text="a"), analysis={"category": "street"})
    for call in msgs.calls:
        assert "street photography in Pune" in call["system"] and "wanderlust" in call["system"]
        assert "$" not in call["system"]
    assert "No more than 3 photos of the same mood" in msgs.calls[0]["system"]


def test_multi_photo_requests_show_every_photo_in_order():
    from instapostsforge.agents import SetPhoto

    wf, msgs = workflow(
        CaptionDraft(caption="Three frames, one morning.", alt_text="cover", more_alt_texts=["second"]),
        ReviewResult(checks=[], verdict="approve"),
        Plan(order=[PlannedPhoto(photo_id="a", extra_photo_ids=["b", "c"], reason="r", angle="g")]),
    )
    more = [SetPhoto(b"jpg2", {"main_subject": "deer"}), SetPhoto(b"jpg3", {"main_subject": "lake"})]
    caption = wf.write_caption(b"jpg1", analysis={"main_subject": "tiger"}, angle="a", recent_captions=[], top_captions=[], more_photos=more)
    assert caption.more_alt_texts == ["second", ""]  # a missing alt text is left empty, never made up
    parts = msgs.calls[0]["parts"]
    assert [p.jpeg for p in parts if isinstance(p, Image)] == [b"jpg1", b"jpg2", b"jpg3"]
    assert "multi-photo post of 3 photos" in parts[-1].text and "deer" in parts[-1].text
    assert wf.review(b"jpg1", caption=caption, analysis={}, more_photos=more).approved
    assert sum(isinstance(p, Image) for p in msgs.calls[1]["parts"]) == 3
    item = wf.plan([{"photo_id": "a"}], [], today=date(2026, 10, 25), horizon=1, photo_sets={"sets": []})[0]
    assert item.extra_ids == ["b", "c"] and "photo_sets" in msgs.calls[2]["parts"][0].text


def test_refusal_and_truncation_raise_and_are_logged_as_failed(tmp_path):
    import pytest

    from instapostsforge.usage import UsageLog

    log = UsageLog("p", state_dir=tmp_path)
    wf, _ = workflow(
        ModelReply(parsed=None, stop="refused", detail="stop_reason=refusal"),
        ModelReply(parsed=None, stop="too_long", detail="stop_reason=max_tokens"),
        usage_log=log,
    )
    with pytest.raises(RuntimeError, match="declined"):
        wf.analyze(b"jpg", photo_name="a.jpg")
    with pytest.raises(RuntimeError, match="No usable PhotoAnalysis"):
        wf.analyze(b"jpg", photo_name="a.jpg")
    assert [e["ok"] for e in log.entries] == [False, False] and log.entries[1]["error"] == "stop_reason=max_tokens"


def test_analyst_sends_the_photo_then_the_file_name():
    from instapostsforge.agents.schemas import PhotoAnalysis

    analysis = PhotoAnalysis(
        category="sky", mood="calm", subject="moon", main_subject="moon", description="d", setting="s", colors=[],
        quality_score=7, has_people=False, has_children=False, has_identifiable_strangers=False, has_text_or_logo=False,
        is_screenshot_or_meme=False, shows_private_home=False, themes=[],
    )
    wf, msgs = workflow(analysis)
    assert wf.analyze(b"jpg", photo_name="moon.jpg")["main_subject"] == "moon"
    first, second = msgs.calls[0]["parts"]
    assert first == Image(b"jpg") and isinstance(second, Text) and "moon.jpg" in second.text
    assert msgs.calls[0]["schema"] is PhotoAnalysis
