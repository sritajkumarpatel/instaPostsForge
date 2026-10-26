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


def test_capture_time_from_file_name_and_exif():
    import io

    from PIL import Image

    from instapostsforge.imaging import name_time, prepare_image

    assert name_time("IMG20261018104837.jpg").isoformat() == "2026-10-18T10:48:37"
    assert name_time("PXL_20261018_104837123.jpg").isoformat() == "2026-10-18T10:48:37"
    assert name_time("holiday.jpg") is None and name_time("IMG20261399999999.jpg") is None

    image = Image.new("RGB", (40, 40))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = "2026:10:20 18:25:18"
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif)
    assert prepare_image(out.getvalue(), {}).taken_at.isoformat() == "2026-10-20T18:25:18"


def test_every_step_sees_when_the_photo_was_taken(tmp_path, distinct_jpeg):
    files = {"IMG20261018104837.jpg": distinct_jpeg(1)}
    ai = FakeAI()
    preview(tmp_path, files, ai)
    assert ai.calls["caption"][0]["analysis"]["taken_at"] == "Sunday 18 October 2026, 10:48"
    assert ai.calls["plan"][0]["candidates"][0]["taken_at"] == "Sunday 18 October 2026, 10:48"


def test_hashtag_feedback_keeps_the_caption(tmp_path, photos):
    from instapostsforge.agents import Review

    ai = FakeAI(
        captions=["Wings like glass.", "Something else."],
        tag_sets=[["#a1", "#b1", "#c1"], ["#a2", "#b2", "#c2"]],
        reviews=[Review(False, "hashtags: too generic", failed=["hashtags"]), Review(True)],
    )
    result, _ = preview(tmp_path, photos, ai)
    assert result.caption.text == "Wings like glass." and result.caption.hashtags == ["#a2", "#b2", "#c2"]
    assert len(ai.calls["caption"]) == 1 and len(ai.calls["tags"]) == 2


def test_caption_feedback_keeps_the_hashtags(tmp_path, photos):
    from instapostsforge.agents import Review

    ai = FakeAI(
        captions=["One October morning.", "One quiet morning."],
        tag_sets=[["#a1", "#b1", "#c1"], ["#a2", "#b2", "#c2"]],
        reviews=[Review(False, "accuracy: month not supported", failed=["accuracy"]), Review(True)],
    )
    result, _ = preview(tmp_path, photos, ai)
    assert result.caption.text == "One quiet morning." and result.caption.hashtags == ["#a1", "#b1", "#c1"]
    assert len(ai.calls["tags"]) == 1


def test_main_subject_never_repeats_within_three_days_even_across_categories(tmp_path, photos):
    analyses = {
        "a.jpg": {"category": "sky", "main_subject": "moon"},
        "b.jpg": {"category": "night", "main_subject": "Moon "},
        "c.jpg": {"category": "animals", "main_subject": "dog"},
    }
    pipeline.run(make_profile(), components(tmp_path, photos, FakeAI(plan_order=["a.jpg"], analyses=analyses)), publish=True)
    ai = FakeAI(plan_order=["b.jpg", "c.jpg"], analyses=analyses)
    result = pipeline.run(make_profile(), components(tmp_path, photos, ai), publish=True)
    assert result.photo.id == "c.jpg"
    assert {"main_subject": "moon"}.items() <= ai.calls["plan"][0]["history"][-1].items()


def test_repeated_subject_goes_ahead_when_nothing_else_is_left(tmp_path, photos):
    files = {"a.jpg": photos["a.jpg"], "b.jpg": photos["b.jpg"]}
    analyses = {"a.jpg": {"category": "sky", "main_subject": "moon"}, "b.jpg": {"category": "night", "main_subject": "moon"}}
    pipeline.run(make_profile(), components(tmp_path, files, FakeAI(plan_order=["a.jpg"], analyses=analyses)), publish=True)
    result = pipeline.run(make_profile(), components(tmp_path, files, FakeAI(analyses=analyses)), publish=True)
    assert result.photo.id == "b.jpg"


def test_subject_spacing_across_planned_days(tmp_path, distinct_jpeg):
    files = {f"p{i}.jpg": distinct_jpeg(i) for i in range(4)}
    subjects = ["moon", "moon", "dog", "sunset"]
    categories = ["sky", "night", "animals", "landscape"]
    analyses = {f"p{i}.jpg": {"main_subject": subjects[i], "category": categories[i], "mood": ["calm", "awe", "playful", "joyful"][i]} for i in range(4)}
    ai = FakeAI(plan_order=["p0.jpg", "p1.jpg", "p2.jpg", "p3.jpg"], analyses=analyses)
    plan = pipeline.plan_days(make_profile(), components(tmp_path, files, ai, publish=False), 4)
    order = [analyses[p.id]["main_subject"] for p, _ in plan]
    assert order == ["moon", "dog", "sunset", "moon"]


def test_old_analyses_without_a_main_subject_are_redone(tmp_path, photos):
    c = components(tmp_path, photos, FakeAI(), publish=False)
    c.state.save_analysis("a.jpg", "a.jpg", {"category": "sky", "mood": "calm", "quality_score": 7})
    pipeline.Run(make_profile(), c).analyse_new([pipeline.Photo(id="a.jpg", name="a.jpg")])
    assert c.state.analysis_for("a.jpg")["main_subject"] == "thing a.jpg"


def test_explicit_photos_are_never_planned(tmp_path, photos):
    ai = FakeAI(plan_order=["a.jpg", "b.jpg"], analyses={"a.jpg": {"is_explicit": True}})
    result, _ = preview(tmp_path, photos, ai)
    assert result.photo.id == "b.jpg"
    assert "a.jpg" not in {c["photo_id"] for c in ai.calls["plan"][0]["candidates"]}


def test_long_caption_is_trimmed_in_code_before_review(tmp_path, photos):
    long = "Rain on the glass. " + "The marigold held its colour as the light went. " * 4 + "Who else watches the rain?"
    ai = FakeAI(plan_order=["a.jpg"], captions=[long])
    result, _ = preview(tmp_path, photos, ai)
    assert len(result.caption.text) <= 150
    assert result.caption.text.startswith("Rain on the glass.") and result.caption.text.endswith("Who else watches the rain?")
    assert len(ai.calls["caption"]) == 1


def test_hook_too_long_to_trim_goes_back_to_the_writer(tmp_path, photos):
    ai = FakeAI(plan_order=["a.jpg"], captions=["x" * 200, "Rain on the glass."])
    result, _ = preview(tmp_path, photos, ai)
    assert result.caption.text == "Rain on the glass."
    assert "150" in ai.calls["caption"][1]["feedback"]


def test_length_alone_never_costs_the_day(tmp_path, photos):
    from instapostsforge.agents import Review

    ai = FakeAI(plan_order=["a.jpg"], reviews=[Review(approved=False, feedback="length: about 285", failed=["length"])])
    result, _ = preview(tmp_path, photos, ai)
    assert result.caption.text == "Quiet streets after rain."
    assert len(ai.calls["caption"]) == 1


def test_category_of_yesterdays_posts_is_blocked_even_after_a_second_post(tmp_path, distinct_jpeg):
    from instapostsforge.state import ProfileState

    files = {name: distinct_jpeg(i) for i, name in enumerate(["a.jpg", "b.jpg", "c.jpg", "d.jpg"])}
    analyses = {"a.jpg": {"category": "macro"}, "b.jpg": {"category": "event"}, "c.jpg": {"category": "macro"}, "d.jpg": {"category": "sky", "mood": "joyful"}}
    today = date(2026, 10, 26)
    for pick in ["a.jpg", "b.jpg"]:
        pipeline.run(make_profile(), components(tmp_path, files, FakeAI(plan_order=[pick], analyses=analyses)), publish=True, today=today)
    state = ProfileState("test", tmp_path / "state")
    for post, when in zip(sorted(state.posts.values(), key=lambda p: p["posted_at"]), ["2026-10-25T14:00:00+00:00", "2026-10-26T18:00:00+00:00"]):
        post["posted_at"] = when
    state._save("posts.json", state.posts)
    ai = FakeAI(plan_order=["c.jpg", "d.jpg"], analyses=analyses)
    result = pipeline.run(make_profile(), components(tmp_path, files, ai), publish=True, today=today)
    assert result.photo.id == "d.jpg"
