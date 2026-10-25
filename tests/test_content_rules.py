"""Content rules the pipeline enforces in code, whatever the AI says."""

from datetime import date

import pytest
from fakes import FakeAI
from test_pipeline import components, make_profile

from instapostsforge import pipeline
from instapostsforge.imaging import fingerprint, fingerprint_distance


@pytest.fixture
def photos(distinct_jpeg):
    return {name: distinct_jpeg(i) for i, name in enumerate(["a.jpg", "b.jpg", "c.jpg"])}


def preview(tmp_path, files, ai, profile=None):
    c = components(tmp_path, files, ai, publish=False)
    return pipeline.run(profile or make_profile(), c, publish=False, drafts_dir=tmp_path / "drafts"), c


def test_screenshots_and_children_are_never_planned(tmp_path, photos):
    ai = FakeAI(
        plan_order=["a.jpg", "b.jpg", "c.jpg"],
        analyses={"a.jpg": {"is_screenshot_or_meme": True}, "b.jpg": {"has_children": True}},
    )
    result, _ = preview(tmp_path, photos, ai)
    assert result.photo.id == "c.jpg"
    assert {c["photo_id"] for c in ai.calls["plan"][0]["candidates"]} == {"c.jpg"}


def test_children_allowed_when_the_profile_says_so(tmp_path, photos):
    ai = FakeAI(plan_order=["b.jpg"], analyses={"b.jpg": {"has_children": True}})
    profile = make_profile(selection={"strategy": "ai_planner", "allow_children": True})
    result, _ = preview(tmp_path, photos, ai, profile)
    assert result.photo.id == "b.jpg"


def test_nothing_postable_explains_why(tmp_path, photos):
    ai = FakeAI(analyses={name: {"is_screenshot_or_meme": True} for name in photos})
    with pytest.raises(pipeline.NothingToPost, match="3 screenshots or memes"):
        preview(tmp_path, photos, ai)


def test_low_quality_only_when_nothing_better(tmp_path, photos):
    ai = FakeAI(plan_order=["a.jpg", "b.jpg"], analyses={"a.jpg": {"quality_score": 2}})
    result, _ = preview(tmp_path, photos, ai)
    assert result.photo.id == "b.jpg"

    only_bad = {"a.jpg": photos["a.jpg"]}
    result, _ = preview(tmp_path / "2", only_bad, FakeAI(analyses={"a.jpg": {"quality_score": 2}}))
    assert result.photo.id == "a.jpg" and any("quality" in n for n in result.notes)


def test_near_duplicate_of_recent_post_is_held_back(tmp_path, photos, distinct_jpeg):
    files = {"a.jpg": photos["a.jpg"], "copy.jpg": photos["a.jpg"], "c.jpg": photos["c.jpg"]}
    pipeline.run(make_profile(), components(tmp_path, files, FakeAI(plan_order=["a.jpg"])), publish=True, today=date(2026, 11, 1))
    ai = FakeAI(plan_order=["copy.jpg", "c.jpg"])
    result = pipeline.run(make_profile(), components(tmp_path, files, ai), publish=True, today=date(2026, 11, 2))
    assert result.photo.id == "c.jpg"


def test_category_never_repeats_on_consecutive_days(tmp_path, photos):
    analyses = {"a.jpg": {"category": "food"}, "b.jpg": {"category": "food"}, "c.jpg": {"category": "sky"}}
    pipeline.run(make_profile(), components(tmp_path, photos, FakeAI(plan_order=["a.jpg"], analyses=analyses)), publish=True)
    ai = FakeAI(plan_order=["b.jpg", "c.jpg"], analyses=analyses)
    result = pipeline.run(make_profile(), components(tmp_path, photos, ai), publish=True)
    assert result.photo.id == "c.jpg"


def test_weekly_mood_limit(tmp_path, distinct_jpeg):
    files = {f"p{i}.jpg": distinct_jpeg(i) for i in range(5)}
    analyses = {f"p{i}.jpg": {"category": ["sky", "food"][i % 2], "mood": "calm"} for i in range(4)}
    analyses["p4.jpg"] = {"category": "sky", "mood": "playful"}
    plan = pipeline.plan_days(make_profile(), components(tmp_path, files, FakeAI(analyses=analyses), publish=False), 3)
    moods = [analyses[p.id]["mood"] for p, _ in plan]
    assert moods[:2] == ["calm", "calm"] and moods[2] == "playful"


def test_quote_dropped_for_food(tmp_path, photos):
    ai = FakeAI(plan_order=["a.jpg"], analyses={"a.jpg": {"category": "food"}}, quotes=["Eat well."])
    result, _ = preview(tmp_path, photos, ai)
    assert result.caption.quote is None


def test_private_home_hides_the_place(tmp_path, photos):
    ai = FakeAI(plan_order=["a.jpg"], analyses={"a.jpg": {"place_name": "12 Rose Lane", "shows_private_home": True}})
    preview(tmp_path, photos, ai)
    assert ai.calls["caption"][0]["analysis"]["place_name"] is None


def test_old_analyses_without_category_are_redone(tmp_path, photos):
    _, c = preview(tmp_path, photos, FakeAI())
    c.state.save_analysis("a.jpg", "a.jpg", {"subject": "old", "quality_score": 7})
    ai = FakeAI()
    preview(tmp_path, photos, ai)
    assert ai.calls["analyze"] == ["a.jpg"]


def test_fingerprints(distinct_jpeg, jpeg_factory):
    a, b = distinct_jpeg(1), distinct_jpeg(2)
    assert fingerprint_distance(fingerprint(a), fingerprint(a)) == 0
    assert fingerprint_distance(fingerprint(a), fingerprint(b)) > 6
