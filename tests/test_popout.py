"""The pop-out poster: the page, the choice of style, and a whole run, with a fake cut-out model."""

from datetime import date

import pytest
from fakes import FakeAI, FakeMaskMaker
from PIL import Image, ImageDraw
from test_pipeline import components, make_profile
from test_styles import photo_jpeg, size_of

from instapostsforge import pipeline
from instapostsforge.captions import clean_overlay
from instapostsforge.imaging import palette as pal
from instapostsforge.imaging import popout
from instapostsforge.state import ProfileState
from instapostsforge.styles import StyleRequest, brief_for, choose_look, choose_style, popout_ready

DAY = date(2026, 10, 20)
WORDS = popout.PopoutText("Monsoon diaries", "Into the wild", "A slower kind of freedom", ("Roads", "Forests", "People"))
SUBJECT = {"subject_top": 0.25, "subject_bottom": 0.8, "subject_left": 0.3, "subject_right": 0.7}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("MULTI_PHOTO", "POST_STYLE", "POST_MOOD", "POST_CATEGORY", "POPOUT_MODEL"):
        monkeypatch.delenv(name, raising=False)


def scene(size=(1200, 900)):
    """A striped photo, so every pixel is different, with a subject in the middle."""
    image = Image.new("RGB", size)
    image.putdata([((x * 7) % 256, (y * 5) % 256, (x + y) % 256) for y in range(size[1]) for x in range(size[0])])
    mask = Image.new("L", size, 0)
    w, h = size
    ImageDraw.Draw(mask).ellipse((0.35 * w, 0.25 * h, 0.65 * w, 0.8 * h), fill=255)
    return image, mask


# --- the page -------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["step_down", "step_left", "step_right", "break_frame"])
def test_every_variant_shows_the_photos_own_pixels_and_keeps_text_off_the_subject(variant):
    image, mask = scene()
    placed = popout.popout(image, mask, [variant], WORDS, seed=3)
    assert placed is not None and placed.variant == variant and placed.image.size == (1080, 1350)
    page, shown, full = placed.image.load(), placed.shown.load(), placed.mask.load()
    solid = [(x, y) for y in range(0, 1350, 3) for x in range(0, 1080, 3) if full[x, y] == 255]
    assert solid and all(page[x, y] == shown[x, y] for x, y in solid)  # never a changed pixel
    subject = placed.subject.load()
    for left, top, right, bottom in placed.text_boxes:
        assert not any(subject[x, y] >= 128 for y in range(max(0, top), min(1350, bottom), 2) for x in range(max(0, left), min(1080, right), 2))
    wl, wt, wr, wb = placed.window
    outside = [(x, y) for y in range(0, 1350, 4) for x in range(0, 1080, 4) if subject[x, y] >= 200 and not (wl <= x < wr and wt <= y < wb)]
    assert outside  # the subject really steps out of the window


def test_the_page_falls_back_when_nothing_fits():
    image, mask = scene()
    assert popout.popout(image, mask, [], WORDS) is None


def test_the_title_takes_a_photo_colour_only_when_it_reads():
    paper = (238, 232, 222)
    green = popout.title_ink([(40, 70, 45), (200, 200, 190)], paper)
    assert green == (40, 70, 45) and pal.contrast(green, paper) >= 4.5
    assert pal.contrast(popout.title_ink([(230, 225, 210)], paper), paper) >= 4.5


def test_the_alt_text_always_says_it_is_a_composite():
    assert "not edited" in popout.alt_suffix("step_down")


# --- the choice -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cover", "reason"),
    [
        ({**SUBJECT, "is_devotional": True}, "deity"),
        ({**SUBJECT, "has_children": True}, "children"),
        ({}, "no subject box"),
        ({**SUBJECT, "subject_top": 0.0, "subject_bottom": 1.0, "subject_left": 0.0, "subject_right": 1.0}, "too small or fills"),
    ],
)
def test_the_pop_out_is_only_tried_on_a_clear_subject(cover, reason):
    assert reason in popout_ready(cover)
    assert popout_ready(SUBJECT) is None and "installed" in popout_ready(SUBJECT, available=False)


def test_a_fixed_pop_out_falls_back_to_a_poster_with_a_reason():
    request = StyleRequest(name="popout")
    style, note = choose_style(request, photo_count=1, cover={**SUBJECT, "is_devotional": True}, today=DAY, popout_available=True)
    assert style == "poster" and "deity" in note
    assert choose_style(request, photo_count=1, cover=SUBJECT, today=DAY, popout_available=True) == ("popout", None)
    assert choose_style(request, photo_count=1, cover=SUBJECT, today=DAY)[0] == "poster"  # not installed


def test_any_keeps_the_planners_pop_out_only_when_it_can_be_tried():
    request = StyleRequest()
    assert choose_style(request, photo_count=1, cover=SUBJECT, today=DAY, suggested="popout", popout_available=True)[0] == "popout"
    assert choose_style(request, photo_count=1, cover=SUBJECT, today=DAY, suggested="popout")[0] != "popout"
    assert "popout" in request.options_for_planner()


def test_the_pop_out_look_is_always_a_light_paper():
    for mood in ("dramatic", "awe", "joyful", "calm"):
        look = choose_look("popout", {"mood": mood}, seed=5)
        assert look.backdrop in ("paper", "handmade", "linen", "kraft")


def test_the_writer_brief_and_the_new_words():
    brief = brief_for("popout")
    assert "overlay_kicker" in brief and "overlay_tags" in brief and "never" not in brief.split("\n\n")[0].lower()
    assert clean_overlay({"kicker": "Road trips", "tag1": "Roads", "tag2": "Extraordinarily"}) == {"kicker": "Road trips", "tag1": "Roads"}


def test_the_writer_maps_the_kicker_and_tags():
    from test_agents import workflow

    from instapostsforge.agents.schemas import CaptionDraft

    draft = CaptionDraft(caption="Hi", alt_text="x", overlay_title="Into the wild", overlay_kicker="Monsoon diaries", overlay_tags=["Roads", "Forests"])
    wf, _ = workflow(draft)
    caption = wf.write_caption(b"jpg", analysis={}, angle="", recent_captions=[], top_captions=[], style_brief=brief_for("popout"))
    assert caption.overlay == {"title": "Into the wild", "kicker": "Monsoon diaries", "tag1": "Roads", "tag2": "Forests"}


# --- a whole run --------------------------------------------------------------------------


def run_popout(tmp_path, monkeypatch, maker, analysis=SUBJECT, **profile):
    monkeypatch.setenv("POST_STYLE", "popout")
    ai = FakeAI(analyses={"a.jpg": {"main_subject": "jeep", "category": "travel", "has_people": True, **analysis}})
    c = components(tmp_path, {"a.jpg": photo_jpeg(1, 1200, 900)}, ai)
    c.mask_maker = maker
    return pipeline.run(make_profile(**profile), c, publish=True, today=DAY), c, ai


def test_a_pop_out_post_is_drawn_disclosed_and_recorded(tmp_path, monkeypatch):
    maker = FakeMaskMaker(shapes=((0.35, 0.25, 0.65, 0.8),))
    result, c, ai = run_popout(tmp_path, monkeypatch, maker)
    assert maker.calls == 1 and result.style == "popout"
    assert "editorial poster" in ai.calls["caption"][0]["style_brief"]
    urls, caption, alt = c.publisher.posts[0]
    assert size_of(next(iter(c.host.uploaded.values()))) == (1080, 1350)
    assert "Cut-out layout · photo unedited" in caption and "not edited" in alt
    assert any("Pop-out" in n for n in result.notes)
    assert ProfileState("test", tmp_path / "state").history()[0]["style"] == "popout"


def test_the_composite_line_can_be_switched_off(tmp_path, monkeypatch):
    selection = {"strategy": "ai_planner", "style": {"popout": {"disclose_in_caption": False}}}
    _, c, _ = run_popout(tmp_path, monkeypatch, FakeMaskMaker(shapes=((0.35, 0.25, 0.65, 0.8),)), selection=selection)
    _, caption, alt = c.publisher.posts[0]
    assert "Cut-out layout" not in caption and "not edited" in alt  # the alt text always says it


def test_a_mask_that_fails_its_checks_gives_a_poster(tmp_path, monkeypatch):
    result, c, ai = run_popout(tmp_path, monkeypatch, FakeMaskMaker(shapes=((0.0, 0.1, 1.0, 1.0),)))
    assert result.style == "poster" and any("No pop-out today" in n for n in result.notes)
    assert "warm paper" in ai.calls["caption"][0]["style_brief"]
    assert "Cut-out layout" not in c.publisher.posts[0][1]


def test_a_broken_model_gives_a_poster(tmp_path, monkeypatch):
    def broken(image):
        raise RuntimeError("model file missing")

    result, _, _ = run_popout(tmp_path, monkeypatch, broken)
    assert result.style == "poster" and any("couldn't run" in n for n in result.notes)


def test_without_the_cut_out_installed_the_pop_out_is_never_tried(tmp_path, monkeypatch):
    result, _, _ = run_popout(tmp_path, monkeypatch, None)
    assert result.style == "poster" and any("isn't installed" in n for n in result.notes)
