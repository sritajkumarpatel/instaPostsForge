"""The content policy: blocked topics are caught in code, in the caption, quote, tags and text on the image."""

import pytest
from fakes import FakeAI
from test_pipeline import components, make_profile

from instapostsforge import pipeline
from instapostsforge.captions import CaptionError, CaptionRules, ContentPolicy, clean_overlay, normalize_caption
from instapostsforge.models import Caption

@pytest.fixture
def ten_photos(distinct_jpeg):
    return {f"p{i}.jpg": distinct_jpeg(i) for i in range(10)}


POLICY = ContentPolicy.from_terms(["# a comment", "politic*", "election*", "war", "war zone", "caste*", "terror*", "nude*", "bomb", "riots"])
TAGS = ["#streetphotography", "#rain", "#citylights"]


def test_shipped_list_loads_and_spares_everyday_phrases():
    policy = ContentPolicy.from_settings({"blocked_terms_file": "config/blocked-terms.txt"})
    assert policy.terms and "caste*" in policy.terms
    for fine in [
        "A riot of colours at Holi", "A brahminy kite over the lake", "Visible to the naked eye", "A photobomb by the cat",
        "Bombay chutney on a Sunday", "Warm light on the communal table", "Jai Shri Ram 🙏", "Ganpati Bappa Morya!",
        "Har Har Mahadev", "Diwali lamps on the balcony", "Swarm of bees", "Software and coffee",
    ]:
        assert policy.found_in(fine) == [], fine


@pytest.mark.parametrize("text,term", [
    ("Election day crowds", "election*"), ("Harvest-time politics", "politic*"), ("The war-zone feel", "war zone"),
    ("No caste here", "caste*"), ("terrorists", "terror*"),
])
def test_blocked_terms_match_whole_words_and_wildcards(text, term):
    assert term in POLICY.found_in(text)


def test_short_terms_never_match_inside_words():
    assert POLICY.found_in("Warm light, a swarm of bees, a software bug, a photobomb") == []


def test_hashtags_match_whole_tags_and_wildcard_starts():
    assert POLICY.blocks_tag("#war") and POLICY.blocks_tag("#electionday") and POLICY.blocks_tag("#Politics")
    assert not POLICY.blocks_tag("#warmlight") and not POLICY.blocks_tag("#bombay") and not POLICY.blocks_tag("#brahminykite")


def test_missing_file_means_no_list(tmp_path, caplog):
    policy = ContentPolicy.from_settings({"blocked_terms_file": str(tmp_path / "none.txt")})
    assert policy.found_in("election war") == [] and "not found" in caplog.text


def test_normalize_drops_blocked_tags_and_rejects_blocked_text():
    rules = CaptionRules(min_hashtags=3)
    caption = normalize_caption(Caption("Quiet streets.", [*TAGS, "#war"], "alt"), rules, POLICY)
    assert caption.hashtags == TAGS
    for bad in (
        Caption("Election rally lights.", TAGS, "alt"),
        Caption("Quiet streets.", TAGS, "alt", quote="All is fair in war"),
        Caption("Quiet streets.", TAGS, "alt", overlay={"title": "Terror at dusk"}),
    ):
        with pytest.raises(CaptionError, match="content"):
            normalize_caption(bad, rules, POLICY)


def test_without_a_policy_nothing_changes():
    caption = normalize_caption(Caption("Election rally lights.", TAGS, "alt"), CaptionRules())
    assert caption.text == "Election rally lights."


def test_overlay_text_is_tidied_and_held_to_its_length():
    cleaned = clean_overlay({"title": "Quiet streets 🌧️ #rain", "line": "x" * 81, "note": "  Same roads, @me different stories "})
    assert cleaned == {"title": "Quiet streets", "note": "Same roads, different stories"}


def test_a_blocked_word_sends_the_caption_back_for_a_rewrite(tmp_path, ten_photos):
    ai = FakeAI(captions=["Election night on a quiet street.", "Quiet streets after rain."])
    profile = make_profile(ai={"hashtags": {"min": 3, "max": 5}, "max_revisions": 1, "blocked_terms_file": "config/blocked-terms.txt"})
    result = pipeline.run(profile, components(tmp_path, ten_photos, ai), publish=True)
    assert result.published and result.caption.text == "Quiet streets after rain."
    assert "content" in ai.calls["caption"][1]["feedback"]


def test_a_blocked_word_that_keeps_coming_back_skips_the_day(tmp_path, ten_photos):
    ai = FakeAI(captions=["Election night."] * 5)
    c = components(tmp_path, ten_photos, ai)
    profile = make_profile(ai={"hashtags": {"min": 3, "max": 5}, "max_revisions": 1, "blocked_terms_file": "config/blocked-terms.txt"})
    with pytest.raises(pipeline.ReviewRejected):
        pipeline.run(profile, c, publish=True)
    assert c.publisher.posts == []


def test_photos_about_politics_or_conflict_are_never_posted(tmp_path, distinct_jpeg):
    files = {"rally.jpg": distinct_jpeg(1), "lake.jpg": distinct_jpeg(2)}
    ai = FakeAI(plan_order=["rally.jpg", "lake.jpg"], analyses={"rally.jpg": {"is_sensitive_topic": True}})
    result = pipeline.run(make_profile(), components(tmp_path, files, ai), publish=True)
    assert result.photo.id == "lake.jpg"
    assert [c["photo_id"] for c in ai.calls["plan"][0]["candidates"]] == ["lake.jpg"]
