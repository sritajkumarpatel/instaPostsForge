from datetime import date

import pytest
from fakes import FakeAI, FakeHost, FakePublisher, FakeSource

from instapostsforge import pipeline
from instapostsforge.agents import Review
from instapostsforge.config import Profile
from instapostsforge.state import ProfileState


def make_profile(**overrides):
    base = dict(
        name="test",
        instagram={},
        source={},
        ai={"hashtags": {"min": 3, "max": 5}, "review": True, "max_revisions": 1},
        image={"min_ratio": 0.8, "max_ratio": 1.91},
        selection={"strategy": "ai_planner"},
        hosting={},
    )
    base.update(overrides)
    return Profile(**base)


@pytest.fixture
def ten_photos(jpeg_factory):
    return {f"p{i}.jpg": jpeg_factory(800, 800) for i in range(10)}


def components(tmp_path, files, ai, publish=True, insights=None):
    return pipeline.Components(
        source=FakeSource(files),
        ai=ai,
        state=ProfileState("test", tmp_path / "state"),
        host=FakeHost() if publish else None,
        publisher=FakePublisher(insights) if publish else None,
    )


def test_ten_photos_post_ten_days_without_repeats(tmp_path, ten_photos):
    posted = []
    for day in range(1, 11):
        c = components(tmp_path, ten_photos, FakeAI())  # fresh run each day, state on disk
        result = pipeline.run(make_profile(), c, publish=True, today=date(2026, 11, day))
        posted.append(result.photo.id)
    assert sorted(posted) == sorted(ten_photos)
    with pytest.raises(pipeline.NothingToPost):
        pipeline.run(make_profile(), components(tmp_path, ten_photos, FakeAI()), publish=True)


def test_recycle_starts_over_when_exhausted(tmp_path, jpeg_factory):
    files = {"a.jpg": jpeg_factory(10, 10)}
    profile = make_profile(selection={"strategy": "random", "when_exhausted": "recycle"})
    pipeline.run(profile, components(tmp_path, files, FakeAI()), publish=True)
    result = pipeline.run(profile, components(tmp_path, files, FakeAI()), publish=True)
    assert result.published and (tmp_path / "state" / "test" / "posts_archive.json").exists()


def test_each_photo_is_analysed_only_once(tmp_path, ten_photos):
    ai = FakeAI()
    pipeline.run(make_profile(), components(tmp_path, ten_photos, ai, publish=False), publish=False, drafts_dir=tmp_path)
    assert len(ai.calls["analyze"]) == 10
    ai2 = FakeAI()
    pipeline.run(make_profile(), components(tmp_path, ten_photos, ai2, publish=False), publish=False, drafts_dir=tmp_path)
    assert ai2.calls["analyze"] == []


def test_planner_choice_and_angle_are_used(tmp_path, ten_photos):
    ai = FakeAI(plan_order=["p7.jpg", "p2.jpg"])
    result = pipeline.run(make_profile(), components(tmp_path, ten_photos, ai), publish=True)
    assert result.photo.id == "p7.jpg"
    assert ai.calls["caption"][0]["angle"] == "angle for p7.jpg"
    assert ai.calls["caption"][0]["analysis"]["subject"] == "subject of p7.jpg"


def test_planner_inventing_ids_falls_back_to_random(tmp_path, ten_photos):
    ai = FakeAI(plan_order=["not-a-photo.jpg"])
    result = pipeline.run(make_profile(), components(tmp_path, ten_photos, ai), publish=True)
    assert result.photo.id in ten_photos
    assert any("random" in n for n in result.notes)


def test_planner_failure_falls_back_to_random(tmp_path, ten_photos):
    result = pipeline.run(make_profile(), components(tmp_path, ten_photos, FakeAI(fail={"plan"})), publish=True)
    assert result.published and result.photo.id in ten_photos


def test_reviewer_feedback_triggers_one_revision(tmp_path, ten_photos):
    ai = FakeAI(captions=["First try.", "Second try."], reviews=[Review(False, "Hook is weak"), Review(True)])
    result = pipeline.run(make_profile(), components(tmp_path, ten_photos, ai), publish=True)
    assert result.caption.text == "Second try."
    assert ai.calls["caption"][1]["feedback"] == "Hook is weak"
    assert ai.calls["tags"][1]["feedback"] == "Hook is weak"


def test_rejected_twice_means_no_post(tmp_path, ten_photos):
    ai = FakeAI(reviews=[Review(False, "Quote misattributed"), Review(False, "Still misattributed")])
    c = components(tmp_path, ten_photos, ai)
    with pytest.raises(pipeline.ReviewRejected):
        pipeline.run(make_profile(), c, publish=True)
    assert c.publisher.posts == [] and not c.state.posted_ids()


def test_rule_breaking_hashtags_are_sent_back(tmp_path, ten_photos):
    ai = FakeAI(tag_sets=[["#viral", "#explore"], ["#street", "#rain", "#night"]])
    result = pipeline.run(make_profile(), components(tmp_path, ten_photos, ai), publish=True)
    assert result.caption.hashtags == ["#street", "#rain", "#night"]
    assert "rule" in ai.calls["caption"][1]["feedback"]


def test_publish_records_post_and_cleans_up(tmp_path, ten_photos):
    c = components(tmp_path, ten_photos, FakeAI())
    result = pipeline.run(make_profile(), c, publish=True)
    assert c.host.deleted == list(c.host.uploaded)
    saved = ProfileState("test", tmp_path / "state").posts[result.photo.id]
    assert saved["hashtags"] == ["#streetphotography", "#rain", "#citylights"]
    assert saved["plan_reason"] == f"because {result.photo.id}"


def test_insights_feed_back_into_planner_and_hashtags(tmp_path, ten_photos):
    pipeline.run(make_profile(), components(tmp_path, ten_photos, FakeAI()), publish=True)
    ai = FakeAI()
    pipeline.run(make_profile(), components(tmp_path, ten_photos, ai, insights={"media-1": {"reach": 420}}), publish=True)
    assert ai.calls["plan"][0]["history"][0]["results"] == {"reach": 420}
    assert ai.calls["tags"][0]["stats"]["#rain"] == {"uses": 1, "avg_reach": 420}


def test_preview_posts_nothing(tmp_path, ten_photos):
    c = components(tmp_path, ten_photos, FakeAI(), publish=False)
    result = pipeline.run(make_profile(), c, publish=False, drafts_dir=tmp_path / "drafts")
    assert not result.published and not c.state.posted_ids()
    assert any((tmp_path / "drafts").rglob("plan.txt"))


def test_plan_days_returns_distinct_photos(tmp_path, ten_photos):
    plan = pipeline.plan_days(make_profile(), components(tmp_path, ten_photos, FakeAI(), publish=False), 7)
    assert len({p.id for p, _ in plan}) == 7
