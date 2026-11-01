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


def test_any_is_the_default_and_unknown_styles_fall_back_to_it(monkeypatch, caplog):
    request = style_request({})
    assert request.name == "any" and "single" not in request.any_styles
    monkeypatch.setenv("POST_STYLE", "collage")
    assert style_request({}).name == "any" and "Unknown post style" in caplog.text
    monkeypatch.setenv("POST_STYLE", "auto")  # the older name
    assert style_request({}).name == "any"
    monkeypatch.setenv("POST_STYLE", "single")
    assert style_request({"style": {"name": "poster"}}).name == "single"
    assert style_request({"style": {"auto_styles": ["poster", "single"]}}).any_styles == ["poster", "single"]


def test_any_follows_the_planners_pick_when_it_fits():
    request = StyleRequest("any")
    assert choose_style(request, photo_count=1, cover={}, today=DAY, suggested="poster") == ("poster", None)
    assert choose_style(request, photo_count=4, cover={}, today=DAY, suggested="grid") == ("grid", None)
    # A pick that can't be drawn, or isn't allowed, is replaced by one that suits the photos.
    assert choose_style(request, photo_count=1, cover={"shape": 1.2}, today=DAY, suggested="panorama")[0] in ("gallery", "poster")
    assert choose_style(request, photo_count=1, cover={}, today=DAY, suggested="single")[0] != "single"
    assert choose_style(request, photo_count=1, cover={}, today=DAY, suggested="grid")[0] in ("gallery", "poster")
    assert choose_style(StyleRequest("any", any_styles=["poster"]), photo_count=4, cover={}, today=DAY)[0] == "poster"


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


def test_any_without_a_pick_suits_the_mood_and_avoids_yesterdays_look():
    request = StyleRequest("any", any_styles=["gallery", "poster"])
    for day in range(1, 15):
        style, _ = choose_style(request, photo_count=1, cover={"mood": "nostalgic"}, today=date(2026, 11, day), last_style="poster")
        assert style == "gallery"
    assert choose_style(StyleRequest("any", any_styles=["grid"]), photo_count=4, cover={}, today=DAY)[0] == "grid"
    assert choose_style(StyleRequest("any", any_styles=["single", "panorama"]), photo_count=1, cover={"shape": 1.2}, today=DAY)[0] == "single"


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


def test_single_style_posts_exactly_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "single")
    files = {"a.jpg": photo_jpeg(1)}
    ai = FakeAI()
    c = components(tmp_path, files, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    assert ai.calls["plan"][0]["style_options"] is None
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


def _photo_share(slide, colour=(200, 30, 30)):
    """How much of the slide the solid test photo covers."""
    data = slide.convert("RGB").resize((108, 135)).tobytes()
    pixels = [data[i : i + 3] for i in range(0, len(data), 3)]
    return sum(1 for p in pixels if abs(p[0] - colour[0]) < 40 and p[1] < 90 and p[2] < 90) / len(pixels)


@pytest.mark.parametrize("size,kind", [((1500, 1000), "landscape"), ((800, 1200), "portrait"), ((1000, 1000), "square")])
def test_every_poster_layout_lets_the_photo_fill_most_of_the_page(size, kind):
    photo = layouts.LayoutPhoto(Image.new("RGB", size, (200, 30, 30)))
    for layout in layouts.POSTER_LAYOUTS[kind]:
        for finish in ("plain", "border", "taped", "taped_border"):
            slide = layouts.poster(photo, "Soft hues, quiet sky", "The slow fade of a monsoon evening.", "lavender and salmon", layout=layout, finish=finish)
            assert slide.size == (1080, 1350)
            # A 3:2 photo can cover at most about 40% of a 4:5 page at full width; the old layout gave it 25%.
            assert _photo_share(slide) > 0.37, (layout, finish)


def test_poster_layout_suits_the_shape_and_changes_from_post_to_post():
    assert layouts.poster_layouts(1.5) == layouts.POSTER_LAYOUTS["landscape"]
    assert layouts.poster_layouts(0.75) == layouts.POSTER_LAYOUTS["portrait"]
    photo = layouts.LayoutPhoto(Image.new("RGB", (1500, 1000), (200, 30, 30)))
    looks = {layouts.poster(photo, "Old walls, new light", seed=seed).resize((27, 34)).tobytes() for seed in range(8)}
    assert len(looks) > 2


def test_by_default_the_planner_chooses_the_style(tmp_path):
    ai = FakeAI(styles={"a.jpg": "poster"})
    c = components(tmp_path, {"a.jpg": photo_jpeg(1)}, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    options = ai.calls["plan"][0]["style_options"]
    assert set(options) == {"poster", "gallery", "grid", "story", "panorama"}
    assert result.style == "poster" and "warm paper" in ai.calls["caption"][0]["style_brief"]
    assert ProfileState("test", tmp_path / "state").history()[0]["style"] == "poster"


def test_by_default_a_set_can_become_a_grid_of_mixed_shapes(tmp_path):
    files = {n: photo_jpeg(i, *((600, 750) if i % 2 else (900, 600))) for i, n in enumerate(OUTING)}
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:]}, analyses=subjects(OUTING), styles={OUTING[0]: "grid"})
    selection = {"strategy": "ai_planner", "multi_photo": {"enabled": True, "max_photos": 5}}
    result = pipeline.run(make_profile(selection=selection), components(tmp_path, files, ai), publish=True, today=DAY)
    assert ai.calls["plan"][0]["photo_sets"]["max_photos_per_post"] == 9  # room for a full grid
    assert result.style == "grid" and len(result.more_photos) == 3


def test_planner_is_shown_the_styles_and_its_pick_is_checked():
    import json

    from instapostsforge.agents.schemas import Plan, PlannedPhoto
    from instapostsforge.styles import STYLE_GUIDE

    options = {s: STYLE_GUIDE[s] for s in ("poster", "gallery")}
    wf, msgs = workflow(Plan(order=[PlannedPhoto(photo_id="a", reason="r", angle="g", style="poster"), PlannedPhoto(photo_id="b", reason="r", angle="g", style="collage")]))
    items = wf.plan([{"photo_id": "a"}, {"photo_id": "b"}], [], today=DAY, horizon=2, style_options=options)
    assert [i.style for i in items] == ["poster", None]  # a name outside the options is dropped
    assert json.loads(msgs.calls[0]["parts"][0].text)["post_styles"] == options
