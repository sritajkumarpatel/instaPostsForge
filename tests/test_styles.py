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
    assert style_request({"style": {"name": "palette"}}).name == "single"
    assert style_request({"style": {"auto_styles": ["palette", "single"]}}).any_styles == ["palette", "single"]


def test_retired_styles_fall_back_to_any_with_a_warning(monkeypatch, caplog):
    for name in ("poster", "gallery"):
        monkeypatch.setenv("POST_STYLE", name)
        assert style_request({}).name == "any" and "was retired" in caplog.text
    assert style_request({"style": {"any_styles": ["poster", "gallery", "film"]}}).any_styles == ["film"]


def test_any_follows_the_planners_pick_when_it_fits():
    request = StyleRequest("any")
    assert choose_style(request, photo_count=1, cover={}, today=DAY, suggested="palette") == ("palette", None)
    assert choose_style(request, photo_count=4, cover={}, today=DAY, suggested="grid") == ("grid", None)
    assert choose_style(request, photo_count=4, cover={}, today=DAY, suggested="contact_sheet") == ("contact_sheet", None)
    # A pick that can't be drawn, or isn't allowed, is replaced by one that suits the photos.
    single = {"palette", "details", "film", "notes"}
    assert choose_style(request, photo_count=1, cover={"shape": 1.2}, today=DAY, suggested="panorama")[0] in single
    assert choose_style(request, photo_count=1, cover={}, today=DAY, suggested="single")[0] != "single"
    assert choose_style(request, photo_count=1, cover={}, today=DAY, suggested="grid")[0] in single
    assert choose_style(request, photo_count=1, cover={}, today=DAY, suggested="details")[0] != "details"  # no anchors to crop
    assert choose_style(StyleRequest("any", any_styles=["film"]), photo_count=4, cover={}, today=DAY)[0] == "film"


def test_any_keeps_the_feed_varied():
    request = StyleRequest("any")
    # the planner's pick is turned down when the last post used it
    assert choose_style(request, photo_count=1, cover={}, today=DAY, suggested="film", recent_styles=["film"])[0] != "film"
    # code's own pick avoids both of the last two posts' styles
    for day in range(1, 20):
        style, _ = choose_style(request, photo_count=1, cover={"mood": "calm", "category": "food"}, today=date(2026, 11, day), recent_styles=["palette", "film"])
        assert style not in ("palette", "film")


def test_sequence_is_offered_only_when_the_photos_were_taken_minutes_apart():
    request = StyleRequest("any", any_styles=["sequence", "story"])
    assert choose_style(request, photo_count=4, cover={}, today=DAY, suggested="sequence") == ("sequence", None)
    assert choose_style(request, photo_count=4, cover={}, today=DAY, suggested="sequence", sequence_ok=False)[0] == "story"


def test_run_choices_override_config(monkeypatch):
    selection = {"style": {"name": "film", "mood": "calm"}}
    assert (style_request(selection).name, style_request(selection).mood) == ("film", "calm")
    monkeypatch.setenv("POST_STYLE", "Palette")
    monkeypatch.setenv("POST_MOOD", "nostalgic")
    monkeypatch.setenv("POST_CATEGORY", "street")
    request = style_request(selection)
    assert (request.name, request.mood, request.category) == ("palette", "nostalgic", "street")
    monkeypatch.setenv("POST_STYLE", "variable")  # the workflow's "use the variable" choice leaves config in charge
    monkeypatch.setenv("POST_MOOD", "gloomy")  # not a mood: ignored
    request = style_request(selection)
    assert request.name == "film" and request.mood == "calm"


def test_choose_style_falls_back_rather_than_failing():
    for name in ("grid", "story", "contact_sheet", "seamless", "sequence"):
        assert choose_style(StyleRequest(name), photo_count=1, cover={}, today=DAY)[0] == "palette"
    assert choose_style(StyleRequest("contact_sheet"), photo_count=2, cover={}, today=DAY)[0] == "palette"  # needs three
    assert choose_style(StyleRequest("seamless"), photo_count=2, cover={}, today=DAY) == ("seamless", None)
    assert choose_style(StyleRequest("panorama"), photo_count=1, cover={"shape": 1.33}, today=DAY)[0] == "palette"
    style, note = choose_style(StyleRequest("details"), photo_count=1, cover={}, today=DAY)
    assert style == "palette" and "fewer than two things" in note
    assert choose_style(StyleRequest("panorama"), photo_count=1, cover={"shape": 2.0}, today=DAY) == ("panorama", None)


def test_any_without_a_pick_suits_the_mood_and_avoids_yesterdays_look():
    request = StyleRequest("any", any_styles=["film", "palette"])
    for day in range(1, 15):
        style, _ = choose_style(request, photo_count=1, cover={"mood": "nostalgic"}, today=date(2026, 11, day), last_style="film")
        assert style == "palette"
    assert choose_style(StyleRequest("any", any_styles=["grid"]), photo_count=4, cover={}, today=DAY)[0] == "grid"
    assert choose_style(StyleRequest("any", any_styles=["single", "panorama"]), photo_count=1, cover={"shape": 1.2}, today=DAY)[0] == "single"


def test_grid_sizes():
    assert [grid_size(n) for n in (1, 2, 3, 4, 5, 7, 9, 12)] == [0, 2, 3, 4, 4, 6, 9, 9]


def test_briefs_carry_the_text_rules_only_for_styles_with_text():
    assert brief_for("single") == "" and brief_for("panorama") == ""
    assert brief_for("sequence") == ""  # code writes the times; nothing for the writer
    assert "can't be edited after posting" in brief_for("palette") and "overlay_title" in brief_for("palette")
    assert "grease pencil" in brief_for("contact_sheet") and "hand-drawn line" in brief_for("seamless")


# --- layouts --------------------------------------------------------------------------


def test_every_layout_is_four_by_five():
    photo = layouts.LayoutPhoto(Image.open(io.BytesIO(photo_jpeg(1, 900, 600))))
    tall = layouts.LayoutPhoto(Image.open(io.BytesIO(photo_jpeg(2, 600, 900))))
    for n in (2, 3, 4, 6, 9):
        assert layouts.grid(([photo, tall] * 5)[:n], "Mysuru in blue").size == (1080, 1350)
    assert layouts.title_over(photo, "One slow Sunday").size == (1080, 1350)
    assert layouts.closing("Every road home is a little different.").size == (1080, 1350)


def test_panorama_needs_a_wide_photo():
    assert layouts.panorama(layouts.LayoutPhoto(Image.new("RGB", (1200, 900)))) is None
    parts = layouts.panorama(layouts.LayoutPhoto(Image.new("RGB", (3000, 1000))))
    assert [p.size for p in parts] == [(1080, 1350)] * 3


# --- the workflow -----------------------------------------------------------------------


def test_writer_returns_overlay_text_only_for_a_styled_post():
    draft = CaptionDraft(caption="Hi", alt_text="x", overlay_title="Old walls, new light", overlay_line=" A quiet line. ")
    wf, msgs = workflow(draft, draft)
    plain = wf.write_caption(b"jpg", analysis={}, angle="", recent_captions=[], top_captions=[])
    styled = wf.write_caption(b"jpg", analysis={}, angle="", recent_captions=[], top_captions=[], style_brief=brief_for("palette"))
    assert plain.overlay == {} and "leave every overlay field empty" in msgs.calls[0]["system"]
    assert styled.overlay == {"title": "Old walls, new light", "line": "A quiet line."}
    assert "colours it is made of" in msgs.calls[1]["system"] and "$style_brief" not in msgs.calls[1]["system"]


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


def test_palette_preview_draws_the_photo_then_its_colours(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "palette")
    ai = FakeAI(overlays=[{"title": "Red earth evening"}])
    result = pipeline.run(make_profile(), components(tmp_path, {"a.jpg": photo_jpeg(1)}, ai, publish=False), publish=False, today=DAY, drafts_dir=tmp_path / "drafts")
    draft = tmp_path / "drafts" / "test" / f"{DAY.isoformat()}_a.jpg"
    assert result.style == "palette" and [size_of((draft / f"{n}.jpg").read_bytes()) for n in (1, 2)] == [(1080, 1350)] * 2
    assert "colours it is made of" in ai.calls["caption"][0]["style_brief"]
    assert "Style: palette" in (draft / "plan.txt").read_text() and "Red earth evening" in (draft / "plan.txt").read_text()
    alts = (draft / "alt_text.txt").read_text().splitlines()
    assert alts[0] == "A wet street at night." and alts[1].startswith("The photo above a row of its colours:")


def test_grid_asks_for_a_set_and_posts_a_collage_then_each_photo(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "grid")
    files = {n: photo_jpeg(i, *((600, 750) if i % 2 else (900, 600))) for i, n in enumerate(OUTING)}  # mixed shapes are fine
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:]}, analyses=subjects(OUTING))
    c = components(tmp_path, files, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    brief = ai.calls["plan"][0]["photo_sets"]
    assert "grid" in brief["todays_style"] and brief["max_photos_per_post"] == 9
    urls, _, alts = c.publisher.posts[0]
    assert result.style == "grid" and len(urls) == 5 and alts[0].startswith(("A grid of 4 photos", "4 instant prints"))
    assert all(size_of(j) == (1080, 1350) for j in c.host.uploaded.values())
    state = ProfileState("test", tmp_path / "state")
    assert all(state.is_posted(n) for n in OUTING) and state.history()[0]["style"] == "grid"


def test_grid_without_a_set_becomes_a_palette(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "grid")
    c = components(tmp_path, {"a.jpg": photo_jpeg(1)}, FakeAI())
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    assert result.style == "palette" and any("grid style needs several photos" in n for n in result.notes)
    assert len(c.publisher.posts[0][0]) == 2  # the photo, then its colours


def test_single_photo_styles_turn_sets_off(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "film")
    monkeypatch.setenv("MULTI_PHOTO", "true")
    files = {n: photo_jpeg(i) for i, n in enumerate(OUTING)}
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:]}, analyses=subjects(OUTING))
    result = pipeline.run(make_profile(), components(tmp_path, files, ai), publish=True, today=DAY)
    assert ai.calls["plan"][0]["photo_sets"] is None and result.more_photos == [] and result.style == "film"


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
    monkeypatch.setenv("POST_STYLE", "film")
    ai = FakeAI(overlays=[{"title": "Election lights"}, {"title": "Old walls, new light"}])
    profile = make_profile(ai={"hashtags": {"min": 3, "max": 5}, "max_revisions": 1, "blocked_terms_file": "config/blocked-terms.txt"})
    result = pipeline.run(profile, components(tmp_path, {"a.jpg": photo_jpeg(1)}, ai), publish=True, today=DAY)
    assert result.caption.overlay == {"title": "Old walls, new light"}


def test_by_default_the_planner_chooses_the_style(tmp_path):
    ai = FakeAI(styles={"a.jpg": "film"})
    c = components(tmp_path, {"a.jpg": photo_jpeg(1)}, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    options = ai.calls["plan"][0]["style_options"]
    assert set(options) == {"palette", "details", "film", "panorama", "notes", "grid", "story", "contact_sheet", "seamless", "sequence"}
    assert result.style == "film" and "strip of film" in ai.calls["caption"][0]["style_brief"]
    assert ProfileState("test", tmp_path / "state").history()[0]["style"] == "film"


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

    options = {s: STYLE_GUIDE[s] for s in ("palette", "film")}
    wf, msgs = workflow(Plan(order=[PlannedPhoto(photo_id="a", reason="r", angle="g", style="palette"), PlannedPhoto(photo_id="b", reason="r", angle="g", style="poster")]))
    items = wf.plan([{"photo_id": "a"}, {"photo_id": "b"}], [], today=DAY, horizon=2, style_options=options)
    assert [i.style for i in items] == ["palette", None]  # a name outside the options is dropped
    assert json.loads(msgs.calls[0]["parts"][0].text)["post_styles"] == options
