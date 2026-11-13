"""The carousel styles that open on the photo: palette, details, contact sheet, seamless strip and sequence."""

from datetime import date, datetime

from fakes import FakeAI
from PIL import Image, ImageDraw
from test_pipeline import components, make_profile
from test_styles import DAY, OUTING, clean_env, photo_jpeg, size_of, subjects  # noqa: F401  (clean_env is an autouse fixture)

from instapostsforge import pipeline
from instapostsforge.imaging import carousels
from instapostsforge.imaging import palette as pal
from instapostsforge.imaging.layouts import LayoutPhoto
from instapostsforge.state import ProfileState


def scene(width=1600, height=1200):
    """Sky over a red field with a yellow box: a few distinct colours to find."""
    image = Image.new("RGB", (width, height), (90, 140, 200))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, height // 2, width, height), fill=(170, 60, 40))
    draw.rectangle((width // 3, height // 3, width // 2, height * 2 // 3), fill=(240, 200, 40))
    return LayoutPhoto(image)


# --- the opening slide ----------------------------------------------------------------


def test_the_opening_slide_fills_the_frame_or_shows_the_whole_photo():
    portrait = carousels.opening(LayoutPhoto(Image.new("RGB", (800, 1000), (200, 30, 30))))
    assert portrait.size == (1080, 1350) and portrait.getpixel((2, 2)) == (200, 30, 30)  # full-bleed
    wide = carousels.opening(scene(1600, 900))
    assert wide.size == (1080, 1350)
    assert wide.getpixel((540, 20)) != (90, 140, 200)  # a mat above the photo, nothing cropped away


# --- palette --------------------------------------------------------------------------------


def test_palette_card_names_the_photos_own_colours():
    card, named = carousels.palette_card(scene(), "Red earth evening")
    assert card.size == (1080, 1350) and 3 <= len(named) <= 5
    names = [n for n, _ in named]
    assert all(n in dict(carousels.COLOUR_NAMES) for n in names)
    assert all(code.startswith("#") and len(code) == 7 for _, code in named)


def test_palette_card_never_shows_a_flag_set():
    flag = Image.new("RGB", (900, 900), (255, 153, 51))
    draw = ImageDraw.Draw(flag)
    draw.rectangle((0, 300, 900, 600), fill=(255, 255, 255))
    draw.rectangle((0, 600, 900, 900), fill=(19, 136, 8))
    _, named = carousels.palette_card(LayoutPhoto(flag))
    colours = [tuple(int(code[i : i + 2], 16) for i in (1, 3, 5)) for _, code in named]
    assert not pal.looks_like_flag(colours)


def test_colour_names_have_no_brands_or_flags():
    names = {n for n, _ in carousels.COLOUR_NAMES}
    assert not names & {"saffron", "khaki", "tiffany", "coca"}


# --- details ----------------------------------------------------------------------------------


def test_detail_crops_follow_the_anchors_and_skip_repeats_and_soft_crops():
    big = scene(3000, 3750)
    crops = carousels.detail_boxes(big, [(0.2, 0.2, "the sky"), (0.22, 0.21, "again"), (0.8, 0.8, "the field")])
    assert [c.label for c in crops] == ["the sky", "the field"]  # the second nearly repeats the first
    for crop in crops:
        left, top, right, bottom = crop.box
        assert 0 <= left < right <= 3000 and 0 <= top < bottom <= 3750
        assert abs((right - left) / (bottom - top) - 0.8) < 0.01  # 4:5, like the slide
    assert carousels.detail_boxes(scene(800, 1000), [(0.5, 0.5, None)]) == []  # too small to crop sharply
    slide = carousels.detail_slide(big, crops[0], 1)
    assert slide.size == (1080, 1350)


def test_details_post_opens_on_the_photo_then_each_crop(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "details")
    anchors = [{"name": "tea glass", "x": 0.3, "y": 0.3}, {"name": "kettle", "x": 0.75, "y": 0.75}, {"name": "man", "x": 0.5, "y": 0.5, "is_person": True}]
    ai = FakeAI(analyses={"a.jpg": {"anchors": anchors, "category": "food"}})
    c = components(tmp_path, {"a.jpg": photo_jpeg(1, 2400, 3000)}, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    urls, _, alts = c.publisher.posts[0]
    assert result.style == "details" and len(urls) == 3  # the photo, then two crops; never the person
    assert alts[1] == "A close look at part of the photo: still warm." and alts[2].endswith("six pm light.")
    brief = ai.calls["caption"][0]["style_brief"]
    assert "1. tea glass" in brief and "2. kettle" in brief and "man" not in brief
    assert all(size_of(j) == (1080, 1350) for j in c.host.uploaded.values())


def test_details_of_a_small_photo_becomes_a_palette(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "details")
    anchors = [{"name": "cup", "x": 0.3, "y": 0.3}, {"name": "pot", "x": 0.7, "y": 0.7}]
    c = components(tmp_path, {"a.jpg": photo_jpeg(1)}, FakeAI(analyses={"a.jpg": {"anchors": anchors}}))
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    assert result.style == "palette" and any("couldn't be drawn" in n for n in result.notes)


# --- contact sheet ---------------------------------------------------------------------------


def _red_pixels(image):
    data = image.convert("RGB").resize((216, 270)).tobytes()
    return sum(1 for i in range(0, len(data), 3) if data[i] > 170 and data[i + 1] < 80 and data[i + 2] < 80)


def test_contact_sheet_circles_the_chosen_frame():
    photos = [scene(), scene(900, 1200), scene(1200, 1200)]
    sheet = carousels.contact_sheet(photos, 0, "the keeper", "Mysuru · November 2026", seed=4)
    assert sheet.size == (1080, 1350) and _red_pixels(sheet) > 40
    plain = carousels.contact_sheet(photos, 0, None, None, seed=4)
    assert _red_pixels(plain) < _red_pixels(sheet)  # the title is written in the same red pencil


def test_contact_sheet_post_shows_the_best_then_the_sheet_then_the_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "contact_sheet")
    files = {n: photo_jpeg(i) for i, n in enumerate(OUTING)}
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:]}, analyses=subjects(OUTING), overlays=[{"title": "the keeper"}])
    c = components(tmp_path, files, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    urls, _, alts = c.publisher.posts[0]
    assert result.style == "contact_sheet" and len(urls) == 5
    assert "contact sheet of all 4 photos" in alts[1]
    assert "grease pencil" in ai.calls["caption"][0]["style_brief"]
    state = ProfileState("test", tmp_path / "state")
    assert all(state.is_posted(n) for n in OUTING)


# --- seamless strip -------------------------------------------------------------------------------


def test_seamless_slides_join_up_and_photos_cross_the_edges():
    two = carousels.seamless([scene(), scene(900, 1200)], "One slow Sunday", seed=1)
    three = carousels.seamless([scene(), scene(), scene(900, 1200)], None, seed=1)
    assert [s.size for s in two] == [(1080, 1350)] * 2 and len(three) == 3
    ground = two[0].getpixel((5, 5))
    # some row at the seam is photo on both sides: a print runs across from one slide into the next
    rows = range(300, 1050, 10)
    assert any(two[0].getpixel((1079, y)) != ground and two[1].getpixel((0, y)) != ground for y in rows)


def test_seamless_post_trims_to_four_photos(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "seamless")
    names = [*OUTING, "IMG20261018104500.jpg"]
    files = {n: photo_jpeg(i) for i, n in enumerate(names)}
    ai = FakeAI(plan_order=[names[0]], extras={names[0]: names[1:]}, analyses=subjects(names))
    c = components(tmp_path, files, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    urls, _, alts = c.publisher.posts[0]
    assert result.style == "seamless" and len(result.more_photos) == 3 and len(urls) == 3
    assert alts[0].startswith("Part 1 of 3 of one strip of 4 photos")
    assert not ProfileState("test", tmp_path / "state").is_posted(names[4])  # left for another day


# --- sequence -----------------------------------------------------------------------------------------


def test_sequence_order_needs_close_times():
    t = [datetime(2026, 11, 1, 18, 9), datetime(2026, 11, 1, 18, 2), datetime(2026, 11, 1, 18, 15)]
    assert carousels.sequence_order(t) == [0, 1, 2]  # the cover first, then the rest by time
    assert carousels.sequence_order([t[2], t[0], t[1]]) == [0, 2, 1]
    assert carousels.sequence_order([t[0], None, t[1]]) is None
    assert carousels.sequence_order([t[0], datetime(2026, 11, 1, 23, 0)]) is None  # hours apart: not one scene changing
    assert carousels.time_label(datetime(2026, 11, 1, 18, 2)) == "6:02 pm"


def test_sequence_post_shows_the_time_on_each_photo(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "sequence")
    files = {n: photo_jpeg(i) for i, n in enumerate(OUTING)}
    ai = FakeAI(plan_order=[OUTING[2]], extras={OUTING[2]: [OUTING[0], OUTING[3], OUTING[1]]}, analyses=subjects(OUTING))
    c = components(tmp_path, files, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    urls, _, alts = c.publisher.posts[0]
    assert result.style == "sequence" and len(urls) == 4
    assert [a[a.rindex("(") :] for a in alts] == ["(10:15 am)", "(9:00 am)", "(9:30 am)", "(10:30 am)"]
    assert ai.calls["caption"][0]["style_brief"] == ""  # nothing for the writer: code writes the times


def test_a_sequence_without_close_times_is_told_as_a_story(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "sequence")
    names = ["IMG20261018090000.jpg", "IMG20261018113000.jpg", "IMG20261018140000.jpg"]  # one outing, hours long
    files = {n: photo_jpeg(i) for i, n in enumerate(names)}
    ai = FakeAI(plan_order=[names[0]], extras={names[0]: names[1:]}, analyses=subjects(names))
    result = pipeline.run(make_profile(), components(tmp_path, files, ai), publish=True, today=date(2026, 10, 20))
    assert result.style == "story" and any("capture times" in n for n in result.notes)
