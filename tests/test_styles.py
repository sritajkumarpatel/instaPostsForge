"""Post styles: the choice, the planner's brief, the layouts, publishing and recording. Single stays as before."""

import io
from datetime import date

import pytest
from fakes import FakeAI
from PIL import Image
from test_pipeline import components, make_profile

from instapostsforge import pipeline
from instapostsforge.agents.schemas import CaptionDraft
from instapostsforge.imaging import layouts
from instapostsforge.models import Caption
from instapostsforge.state import ProfileState
from instapostsforge.styles import StyleRequest, brief_for, choose_style, grid_size, style_request
from test_agents import workflow

OUTING = ["IMG20261018090000.jpg", "IMG20261018093000.jpg", "IMG20261018101500.jpg", "IMG20261018103000.jpg"]
DAY = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("MULTI_PHOTO", "POST_STYLE", "POST_MOOD", "POST_CATEGORY"):
        monkeypatch.delenv(name, raising=False)


def photo_jpeg(seed, width=600, height=750):
    import random

    rng = random.Random(seed)
    small = Image.new("RGB", (8, 10))
    small.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(80)])
    out = io.BytesIO()
    small.resize((width, height)).save(out, format="JPEG")
    return out.getvalue()


def size_of(jpeg):
    return Image.open(io.BytesIO(jpeg)).size


def subjects(names, **extra):
    return {n: {"main_subject": f"thing{i}", "category": "landscape", **extra} for i, n in enumerate(names)}


# --- the choice -------------------------------------------------------------------


def test_single_is_the_default_and_unknown_styles_fall_back_to_it(monkeypatch, caplog):
    assert style_request({}).name == "single"
    monkeypatch.setenv("POST_STYLE", "collage")
    assert style_request({}).name == "single" and "Unknown post style" in caplog.text


def test_run_choices_override_config(monkeypatch):
    selection = {"style": {"name": "gallery", "mood": "calm"}}
    assert (style_request(selection).name, style_request(selection).mood) == ("gallery", "calm")
    monkeypatch.setenv("POST_STYLE", "Poster")
    monkeypatch.setenv("POST_MOOD", "nostalgic")
    monkeypatch.setenv("POST_CATEGORY", "street")
    request = style_request(selection)
    assert (request.name, request.mood, request.category) == ("poster", "nostalgic", "street")
    monkeypatch.setenv("POST_STYLE", "variable")  # the workflow's "use the variable" choice leaves config in charge
    monkeypatch.setenv("POST_MOOD", "gloomy")  # not a mood: ignored
    request = style_request(selection)
    assert request.name == "gallery" and request.mood == "calm"


def test_choose_style_falls_back_rather_than_failing():
    assert choose_style(StyleRequest("grid"), photo_count=1, cover={}, today=DAY)[0] == "gallery"
    assert choose_style(StyleRequest("story"), photo_count=1, cover={}, today=DAY)[0] == "poster"
    assert choose_style(StyleRequest("panorama"), photo_count=1, cover={"shape": 1.33}, today=DAY)[0] == "gallery"
    assert choose_style(StyleRequest("panorama"), photo_count=1, cover={"shape": 2.0}, today=DAY) == ("panorama", None)


def test_auto_suits_the_mood_and_avoids_yesterdays_look():
    request = StyleRequest("auto", auto_styles=["gallery", "poster"])
    for day in range(1, 15):
        style, _ = choose_style(request, photo_count=1, cover={"mood": "nostalgic"}, today=date(2026, 11, day), last_style="poster")
        assert style == "gallery"
    assert choose_style(StyleRequest("auto", auto_styles=["grid"]), photo_count=4, cover={}, today=DAY)[0] == "grid"
    assert choose_style(StyleRequest("auto", auto_styles=["single", "panorama"]), photo_count=1, cover={"shape": 1.2}, today=DAY)[0] == "single"


def test_grid_sizes():
    assert [grid_size(n) for n in (1, 2, 3, 4, 5, 7, 9, 12)] == [0, 2, 3, 4, 4, 6, 9, 9]


def test_briefs_carry_the_text_rules_only_for_styles_with_text():
    assert brief_for("single") == "" and brief_for("panorama") == ""
    assert "can't be edited after posting" in brief_for("poster") and "overlay_note" in brief_for("poster")


# --- layouts --------------------------------------------------------------------------


def test_every_layout_is_four_by_five():
    photo = layouts.LayoutPhoto(Image.open(io.BytesIO(photo_jpeg(1, 900, 600))))
    tall = layouts.LayoutPhoto(Image.open(io.BytesIO(photo_jpeg(2, 600, 900))))
    for n in (2, 3, 4, 6, 9):
        assert layouts.grid(([photo, tall] * 5)[:n], "Mysuru in blue").size == (1080, 1350)
    assert layouts.gallery(tall, "After the rain", "Bengaluru · October 2026").size == (1080, 1350)
    assert layouts.poster(tall, "Small towns, big dreams", "Not just a delivery.", "Same roads, different stories").size == (1080, 1350)
    assert layouts.title_over(photo, "One slow Sunday").size == (1080, 1350)
    assert layouts.closing("Every road home is a little different.").size == (1080, 1350)


def test_panorama_needs_a_wide_photo():
    assert layouts.panorama(layouts.LayoutPhoto(Image.new("RGB", (1200, 900)))) is None
    parts = layouts.panorama(layouts.LayoutPhoto(Image.new("RGB", (3000, 1000))))
    assert [p.size for p in parts] == [(1080, 1350)] * 3


def test_decorations_come_only_from_the_owners_folder(tmp_path):
    assert layouts.decor(str(tmp_path / "missing"), 1) is None
    Image.new("RGBA", (50, 50), (0, 128, 0, 200)).save(tmp_path / "leaf.png")
    assert layouts.decor(str(tmp_path), 1).mode == "RGBA"


# --- the workflow -----------------------------------------------------------------------


def test_writer_returns_overlay_text_only_for_a_styled_post():
    draft = CaptionDraft(caption="Hi", alt_text="x", overlay_title="Old walls, new light", overlay_line=" A quiet line. ")
    wf, msgs = workflow(draft, draft)
    plain = wf.write_caption(b"jpg", analysis={}, angle="", recent_captions=[], top_captions=[])
    styled = wf.write_caption(b"jpg", analysis={}, angle="", recent_captions=[], top_captions=[], style_brief=brief_for("poster"))
    assert plain.overlay == {} and "leave overlay_title" in msgs.calls[0]["system"]
    assert styled.overlay == {"title": "Old walls, new light", "line": "A quiet line."}
    assert "warm paper" in msgs.calls[1]["system"] and "$style_brief" not in msgs.calls[1]["system"]


def test_reviewer_sees_the_text_on_the_image():
    from instapostsforge.agents.schemas import ReviewResult

    wf, msgs = workflow(ReviewResult(checks=[], verdict="approve"))
    wf.review(b"jpg", caption=Caption("c", ["#a"], "alt", overlay={"title": "Old walls"}))
    assert 'title: "Old walls"' in msgs.calls[0]["parts"][-1].text


# --- the pipeline ---------------------------------------------------------------------------


def test_single_style_posts_exactly_as_before(tmp_path):
    files = {"a.jpg": photo_jpeg(1)}
    ai = FakeAI()
    c = components(tmp_path, files, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    assert result.style == "single" and result.caption.overlay == {}
    assert ai.calls["caption"][0]["style_brief"] == ""
    assert list(c.host.uploaded) == [f"test-{pipeline.photo_key('a.jpg')[2:]}.jpg"]
    assert "style" not in ProfileState("test", tmp_path / "state").posts[pipeline.photo_key("a.jpg")]


def test_poster_preview_draws_one_slide_with_the_writers_text(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "poster")
    ai = FakeAI(overlays=[{"title": "Small towns, big dreams", "line": "A quiet line.", "note": "Same roads"}])
    result = pipeline.run(make_profile(), components(tmp_path, {"a.jpg": photo_jpeg(1)}, ai, publish=False), publish=False, today=DAY, drafts_dir=tmp_path / "drafts")
    draft = tmp_path / "drafts" / "test" / f"{DAY.isoformat()}_a.jpg"
    assert result.style == "poster" and size_of((draft / "image.jpg").read_bytes()) == (1080, 1350)
    assert "warm paper" in ai.calls["caption"][0]["style_brief"]
    assert "Style: poster" in (draft / "plan.txt").read_text() and "Small towns, big dreams" in (draft / "plan.txt").read_text()


def test_grid_asks_for_a_set_and_posts_a_collage_then_each_photo(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "grid")
    files = {n: photo_jpeg(i, *((600, 750) if i % 2 else (900, 600))) for i, n in enumerate(OUTING)}  # mixed shapes are fine
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:]}, analyses=subjects(OUTING))
    c = components(tmp_path, files, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    brief = ai.calls["plan"][0]["photo_sets"]
    assert "grid" in brief["todays_style"] and brief["max_photos_per_post"] == 9
    urls, _, alts = c.publisher.posts[0]
    assert result.style == "grid" and len(urls) == 5 and alts[0].startswith("A grid of 4 photos")
    assert all(size_of(j) == (1080, 1350) for j in c.host.uploaded.values())
    state = ProfileState("test", tmp_path / "state")
    assert all(state.is_posted(n) for n in OUTING) and state.history()[0]["style"] == "grid"


def test_grid_without_a_set_becomes_a_gallery_frame(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "grid")
    c = components(tmp_path, {"a.jpg": photo_jpeg(1)}, FakeAI())
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    assert result.style == "gallery" and any("grid style needs several photos" in n for n in result.notes)
    assert isinstance(c.publisher.posts[0][0], str)  # one slide, posted as a single image


def test_single_photo_styles_turn_sets_off(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "gallery")
    monkeypatch.setenv("MULTI_PHOTO", "true")
    files = {n: photo_jpeg(i) for i, n in enumerate(OUTING)}
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:]}, analyses=subjects(OUTING))
    result = pipeline.run(make_profile(), components(tmp_path, files, ai), publish=True, today=DAY)
    assert ai.calls["plan"][0]["photo_sets"] is None and result.more_photos == [] and result.style == "gallery"


def test_panorama_splits_a_wide_photo_into_joined_slides(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "panorama")
    c = components(tmp_path, {"wide.jpg": photo_jpeg(1, 2000, 800)}, FakeAI())
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    urls, _, alts = c.publisher.posts[0]
    assert result.style == "panorama" and len(urls) == 3 and alts[1].endswith("(part 2 of 3)")


def test_story_ends_with_the_closing_line(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "story")
    files = {n: photo_jpeg(i) for i, n in enumerate(OUTING[:3])}
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:3]}, analyses=subjects(OUTING[:3]))
    c = components(tmp_path, files, ai)
    pipeline.run(make_profile(), c, publish=True, today=DAY)
    urls, _, alts = c.publisher.posts[0]
    assert len(urls) == 4 and alts[-1] == "The city after rain."


def test_mood_choice_narrows_the_photos_and_never_blocks_the_day(tmp_path, monkeypatch):
    files = {"calm.jpg": photo_jpeg(1), "loud.jpg": photo_jpeg(2)}
    analyses = {"calm.jpg": {"mood": "calm"}, "loud.jpg": {"mood": "energetic"}}
    monkeypatch.setenv("POST_MOOD", "energetic")
    ai = FakeAI(analyses=analyses)
    result = pipeline.run(make_profile(), components(tmp_path, files, ai), publish=True, today=DAY)
    assert result.photo.id == "loud.jpg" and [c["photo_id"] for c in ai.calls["plan"][0]["candidates"]] == ["loud.jpg"]
    monkeypatch.setenv("POST_MOOD", "romantic")
    result = pipeline.run(make_profile(), components(tmp_path, files, FakeAI(analyses=analyses)), publish=True, today=DAY)
    assert result.published and any("No unposted photo has mood romantic" in n for n in result.notes)


def test_overlay_text_with_a_blocked_word_is_rewritten(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "poster")
    ai = FakeAI(overlays=[{"title": "Election lights"}, {"title": "Old walls, new light"}])
    profile = make_profile(ai={"hashtags": {"min": 3, "max": 5}, "max_revisions": 1, "blocked_terms_file": "config/blocked-terms.txt"})
    result = pipeline.run(profile, components(tmp_path, {"a.jpg": photo_jpeg(1)}, ai), publish=True, today=DAY)
    assert result.caption.overlay == {"title": "Old walls, new light"}
