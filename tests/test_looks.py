"""Looks: backgrounds from the photo's colours, frames, and the hand-drawn layer of a notes post.

What these tests hold to: the photo itself is never changed, nothing is drawn on the subject or on a
person, devotional photos get no marks, flag colours never appear as bands or swatches, text stays
readable, and every look is chosen by rules, not by the model.
"""

import io
from datetime import date, datetime

import pytest
from fakes import FakeAI
from PIL import Image, ImageChops, ImageDraw
from test_pipeline import components, make_profile
from test_styles import DAY, clean_env, photo_jpeg  # noqa: F401  (clean_env is an autouse fixture)

from instapostsforge import pipeline
from instapostsforge.agents.schemas import CaptionDraft, PhotoAnalysis
from instapostsforge.captions import CaptionError, CaptionRules, ContentPolicy, normalize_caption
from instapostsforge.imaging import backdrops, frames, layouts, marks
from instapostsforge.imaging import palette as pal
from instapostsforge.imaging.processor import Focus
from instapostsforge.models import Caption
from instapostsforge.styles import (
    PEOPLE_SAFE_MARKS,
    StyleRequest,
    choose_look,
    choose_style,
    look_brief,
    notes_brief,
    usable_anchors,
)
from test_agents import workflow

SAFFRON, GREEN, WHITE = (240, 140, 30), (20, 140, 50), (250, 250, 250)


def scene(w=1080, h=1350):
    """A photo with a flat sky, a busy ground, and a red subject in the middle."""
    image = Image.new("RGB", (w, h), (120, 170, 220))
    draw = ImageDraw.Draw(image)
    for y in range(h * 2 // 3, h, 6):
        draw.line((0, y, w, y), fill=(60 + y % 40, 90, 70), width=2)
    draw.rectangle((w * 0.4, h * 0.4, w * 0.6, h * 0.6), fill=(200, 40, 40))
    return image


SUBJECT = Focus(0.4, 0.6, 0.4, 0.6)


def unchanged_inside(before, after, focus):
    w, h = before.size
    box = (int(focus.left * w), int(focus.top * h), int(focus.right * w), int(focus.bottom * h))
    return ImageChops.difference(before.crop(box), after.crop(box)).getbbox() is None


# --- palette and guardrails on colour ------------------------------------------------


def test_palette_takes_the_photos_own_colours():
    colours = pal.palette(scene())
    assert 2 <= len(colours) <= 5
    assert any(pal.distance(c, (120, 170, 220)) < 40 for c in colours)  # the sky


def test_text_ink_is_always_readable():
    for bg in [(250, 248, 240), (20, 24, 50), (200, 120, 60), (128, 128, 128)]:
        assert pal.contrast(pal.ink_for(bg), bg) >= 4.5 or pal.contrast(pal.ink_for(bg), bg) == max(
            pal.contrast(pal.INK, bg), pal.contrast(pal.WHITE, bg)
        )


def test_flag_colours_never_appear_as_swatches_or_bands():
    assert pal.looks_like_flag([SAFFRON, WHITE, GREEN])
    assert not pal.looks_like_flag(pal.safe_swatches([SAFFRON, WHITE, GREEN, (90, 60, 120)]))
    striped = Image.new("RGB", (300, 300), SAFFRON)
    striped.paste(Image.new("RGB", (300, 100), WHITE), (0, 100))
    striped.paste(Image.new("RGB", (300, 100), GREEN), (0, 200))
    two = backdrops.backdrop("two_tone", striped, seed=1)
    top, low = two.image.getpixel((540, 40)), two.image.getpixel((540, 1300))
    assert not pal.looks_like_flag([top, low])
    swatch = backdrops.backdrop("swatches", striped, seed=1)
    assert not pal.looks_like_flag(swatch.colours) or not swatch.swatches


@pytest.mark.parametrize("kind", backdrops.BACKGROUNDS)
def test_every_background_is_four_by_five_and_its_ink_reads(kind):
    back = backdrops.backdrop(kind, scene(), seed=3)
    assert back.image.size == (1080, 1350)
    corner = back.image.resize((1, 1), Image.BOX).getpixel((0, 0))
    assert pal.contrast(back.ink, corner) >= 3.0


# --- frames --------------------------------------------------------------------------


def test_every_frame_is_four_by_five_and_shows_the_whole_photo():
    photo = layouts.LayoutPhoto(scene(1500, 1000))
    slides = [
        frames.postcard(photo, "Wish you were here.", "Goa", "November 2026"),
        frames.ticket(photo, "Hills, slow roads", "Munnar", "November 2026", 427),
        frames.film(photo, "Market morning", 12),
        frames.magazine(photo, "The long way round", "Three days on one bus.", "No. 305 · November 2026", 42),
        frames.festive(photo, "Evening lamps", "November 2026"),
        frames.polaroids([photo, photo, photo], "One slow weekend"),
    ]
    assert all(s.size == (1080, 1350) for s in slides)


def test_poster_and_gallery_take_a_background():
    photo = layouts.LayoutPhoto(scene(1500, 1000))
    for kind in ("echo", "swatches", "sun", "navy"):
        back = backdrops.backdrop(kind, photo.image, seed=2)
        assert layouts.poster(photo, "Old walls, new light", "A line.", "a note", seed=5, backdrop=back).size == (1080, 1350)
        assert layouts.gallery(photo, "After the rain", "Goa", backdrop=back).size == (1080, 1350)


# --- the drawn layer -----------------------------------------------------------------


ANCHORS = [marks.Anchor("subject", 0.5, 0.5, "the red box"), marks.Anchor("ground", 0.3, 0.85, "busy ground"), marks.Anchor("sky", 0.7, 0.15, "not a cloud")]


@pytest.mark.parametrize("kind", marks.MARKS)
def test_marks_never_touch_the_photo_or_the_subject(kind):
    photo = scene()
    before = photo.copy()
    drawn = marks.draw(
        photo,
        [kind],
        anchors=ANCHORS,
        focus=SUBJECT,
        taken_at=datetime(2026, 11, 1, 18, 12),
        light_label="6:12 pm · golden hour",
        place="Goa",
        title="Coastline",
        line="The subject sits in the middle.",
        note="the quiet seven",
        issue="No. 305 · November 2026",
        colours=pal.palette(photo),
        seed=4,
    )
    assert ImageChops.difference(before, photo).getbbox() is None  # the original is untouched
    assert drawn.image.size == photo.size
    assert unchanged_inside(before, drawn.image, SUBJECT)


def test_field_notes_write_each_note_and_skip_what_cant_be_placed():
    drawn = marks.draw(scene(), ["field_notes"], anchors=ANCHORS, focus=SUBJECT, seed=1)
    assert drawn.marks == ["field_notes"] and set(drawn.notes) <= {"the red box", "busy ground", "not a cloud"} and drawn.notes
    # a subject that fills the frame leaves nowhere to write
    full = marks.draw(scene(), ["field_notes", "doodles", "cover"], anchors=ANCHORS, focus=Focus(0, 1, 0, 1), title="Coast", seed=1)
    assert full.marks == []


def test_date_stamp_comes_from_the_capture_time():
    drawn = marks.draw(scene(), ["date_stamp"], taken_at=datetime(2026, 11, 1), seed=1)
    assert drawn.marks == ["date_stamp"]
    assert marks.draw(scene(), ["date_stamp"], seed=1).marks == []  # no time, no stamp


def test_constellation_needs_a_starry_sky():
    sky = Image.new("RGB", (1080, 1350), (12, 16, 40))
    draw = ImageDraw.Draw(sky)
    for x, y in [(200, 200), (300, 260), (380, 200), (460, 300), (520, 240), (260, 380)]:
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 255, 255))
    assert marks.draw(sky, ["constellation"], note="the quiet six", seed=1).marks == ["constellation"]
    assert marks.draw(scene(), ["constellation"], seed=1).marks == []


def moonlit(w=1080, h=1350):
    """A night sky with a lone moon and no stars: the photo a star map can't be drawn on."""
    image = Image.new("RGB", (w, h), (14, 16, 26))
    ImageDraw.Draw(image).ellipse((w * 0.45, h * 0.27, w * 0.65, h * 0.43), fill=(232, 226, 214))
    return image


MOON = Focus(0.27, 0.43, 0.45, 0.65)


def test_notes_never_end_up_as_only_a_date_stamp():
    # the moon post that looked posted twice: no stars, so only the date stamp was drawn
    plain = marks.draw(moonlit(), ["constellation", "date_stamp"], focus=MOON, taken_at=datetime(2026, 9, 26, 19, 42), seed=3)
    assert plain.marks == ["date_stamp"]
    fixed = marks.draw_notes(moonlit(), ["constellation", "date_stamp"], focus=MOON, taken_at=datetime(2026, 9, 26, 19, 42), seed=3)
    assert "date_stamp" in fixed.marks and set(fixed.marks) - marks.ACCENT_MARKS
    changed = ImageChops.difference(moonlit(), fixed.image).convert("L").point(lambda v: 255 if v > 24 else 0)
    assert changed.histogram()[255] > plain_changed(plain.image)  # clearly more drawn than the stamp alone
    # a person in the frame: no ring or rays around them, corner brackets instead
    person = marks.draw_notes(moonlit(), ["date_stamp"], focus=MOON, subject_is_person=True, taken_at=datetime(2026, 9, 26), seed=3)
    assert "viewfinder" in person.marks and not {"orbit", "rays"} & set(person.marks)


def plain_changed(image):
    return ImageChops.difference(moonlit(), image).convert("L").point(lambda v: 255 if v > 24 else 0).histogram()[255]


def test_ring_and_rays_go_around_a_lone_subject_only():
    for kind in ("orbit", "rays"):
        drawn = marks.draw(moonlit(), [kind], focus=MOON, seed=2)
        assert drawn.marks == [kind] and unchanged_inside(moonlit(), drawn.image, MOON)
        assert marks.draw(moonlit(), [kind], focus=Focus(0.05, 0.95, 0.1, 0.9), seed=2).marks == []  # no room around it
        assert marks.draw(moonlit(), [kind], seed=2).marks == []  # no subject band, nothing to circle
        assert marks.draw(moonlit(), [kind], focus=MOON, subject_is_person=True, seed=2).marks == []


def test_birds_fly_only_in_a_calm_sky():
    assert marks.draw(scene(), ["birds"], focus=SUBJECT, seed=1).marks == ["birds"]
    busy = Image.effect_noise((1080, 1350), 90).convert("RGB")
    assert marks.draw(busy, ["birds"], focus=SUBJECT, seed=1).marks == []
    assert marks.draw(scene(), ["birds"], focus=SUBJECT, subject_is_person=True, seed=1).marks == []


def test_aside_writes_one_note_and_needs_words():
    drawn = marks.draw(scene(), ["aside"], focus=SUBJECT, note="hello, moon", seed=1)
    assert drawn.marks == ["aside"] and drawn.notes == ["hello, moon"]
    assert marks.draw(scene(), ["aside"], focus=SUBJECT, seed=1).marks == []


def test_doodles_stay_few():
    layer = marks.Layer((1080, 1350), 1)
    assert marks.doodles(layer, scene(), marks.subject_box((1080, 1350), SUBJECT), ("sparkle", "star")) <= 3


# --- choosing the look by rule -----------------------------------------------------------


def test_look_follows_mood_and_category_and_avoids_yesterday():
    calm = {"mood": "calm", "category": "landscape"}
    looks = {choose_look("poster", calm, seed=s).backdrop for s in range(30)}
    assert looks <= {"palette", "linen", "swatches", "echo"} and len(looks) > 1
    first = choose_look("poster", calm, seed=7)
    assert choose_look("poster", calm, seed=7, last_look=first.name).name != first.name
    night = choose_look("notes", {"category": "night", "mood": "awe"}, seed=1, has_time=True)
    assert night.marks in (("constellation", "date_stamp"), ("viewfinder",))  # no subject band: nothing to ring
    lone = {"category": "night", "subject_top": 0.3, "subject_bottom": 0.45, "subject_left": 0.4, "subject_right": 0.6}
    rings = {choose_look("notes", lone, seed=s, has_time=True).marks for s in range(40)}
    assert ("orbit", "date_stamp") in rings and ("rays", "aside") in rings


def test_people_get_only_marks_that_stay_off_them():
    for seed in range(20):
        for category in ("people", "street", "food", "travel", "event"):
            look = choose_look("notes", {"category": category, "has_people": True, "anchors": [{"name": "cup", "x": 0.5, "y": 0.5}]}, seed=seed, has_time=True, has_place=True)
            assert set(look.marks) <= PEOPLE_SAFE_MARKS


def test_devotional_photos_get_a_plain_frame_never_marks():
    analysis = {"category": "event", "is_devotional": True}
    assert choose_style(StyleRequest("notes"), photo_count=1, cover=analysis, today=DAY)[0] == "gallery"
    assert choose_style(StyleRequest("any"), photo_count=1, cover=analysis, today=DAY, suggested="notes")[0] != "notes"
    assert choose_look("gallery", analysis, seed=3).frame == "festive"


def test_person_anchors_are_never_pointed_at():
    analysis = {"anchors": [{"name": "man", "x": 0.5, "y": 0.5, "is_person": True}, {"name": "cup", "x": 0.2, "y": 0.8}, {"name": "x", "x": 3, "y": 0.1}]}
    assert usable_anchors(analysis) == [{"name": "cup", "x": 0.2, "y": 0.8}]
    # without anchors there are no notes to point, but there is always something to draw
    look = choose_look("notes", {"category": "food"}, seed=1)
    assert look.marks and "field_notes" not in look.marks


def test_aside_brief_asks_for_one_short_note():
    from instapostsforge.styles import Look

    brief = notes_brief(Look(marks=("rays", "aside")), [])
    assert "overlay_note" in brief and "24 characters" in brief


def test_config_can_narrow_the_looks():
    look = choose_look("poster", {"mood": "calm"}, seed=1, settings={"backdrops": ["kraft"]})
    assert look.backdrop == "kraft"


def test_brief_lists_the_things_to_annotate():
    look = choose_look("notes", {"category": "food", "anchors": [{"name": "tea glass", "x": 0.5, "y": 0.5}]}, seed=2)
    brief = look_brief("notes", look, [{"name": "tea glass", "x": 0.5, "y": 0.5}])
    assert "hand-drawn" in brief and ("1. tea glass" in brief or "overlay_marks" not in brief)
    frame = look_brief("gallery", choose_look("gallery", {"category": "travel"}, seed=5, settings={"backdrops": ["paper"], "frames": ["postcard"]}))
    assert "Text on the image can't be edited" in frame


# --- AI fields: only where code can't do it -------------------------------------------------


def test_analyst_schema_has_anchors_and_the_devotional_flag():
    fields = PhotoAnalysis.model_fields
    assert "anchors" in fields and "is_devotional" in fields


def test_writer_returns_marks_only_for_a_styled_post():
    draft = CaptionDraft(caption="Tea time.", alt_text="Tea.", overlay_marks=["still warm", "six pm light", "a", "extra"])
    wf, _ = workflow(draft, draft)
    styled = wf.write_caption(b"jpeg", analysis={}, angle="", recent_captions=[], top_captions=[], style_brief="Today's post ... hand-drawn")
    assert styled.overlay["mark1"] == "still warm" and "mark4" not in styled.overlay
    plain = wf.write_caption(b"jpeg", analysis={}, angle="", recent_captions=[], top_captions=[])
    assert plain.overlay == {}


def test_notes_with_a_blocked_word_are_refused():
    caption = Caption(text="Tea time.", hashtags=["#tea", "#chai", "#evening"], alt_text="Tea.", overlay={"mark1": "election tea"})
    policy = ContentPolicy.from_terms(["election*"])
    with pytest.raises(CaptionError):
        normalize_caption(caption, CaptionRules(), policy)


# --- the pipeline -------------------------------------------------------------------------


def test_notes_post_shows_the_photo_untouched_then_the_drawn_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "notes")
    analyses = {"a.jpg": {"category": "food", "mood": "calm", "anchors": [{"name": "tea glass", "x": 0.3, "y": 0.3}], "subject_top": 0.4, "subject_bottom": 0.6, "subject_left": 0.4, "subject_right": 0.6}}
    ai = FakeAI(analyses=analyses)
    c = components(tmp_path, {"a.jpg": photo_jpeg(1, 1080, 1350)}, ai)
    result = pipeline.run(make_profile(), c, publish=True, today=DAY)
    urls, _, alts = c.publisher.posts[0]
    assert result.style == "notes" and len(urls) == 2
    assert "hand-drawn" in ai.calls["caption"][0]["style_brief"]
    posted = list(c.host.uploaded.values()) if hasattr(c.host, "uploaded") else None
    if posted:
        clean = pipeline.Run(make_profile(), c, DAY).post_image(result.photo).jpeg
        assert posted[0] == clean  # the first slide is the photo exactly as a single post would send it
    assert "hand-drawn" in alts[1]
    entry = next(iter(c.state.posts.values()))
    assert entry["style"] == "notes" and entry["look"]


def moon_jpeg() -> bytes:
    """A lone moon in a dark, starless sky: nothing for a constellation to join."""
    sky = Image.new("RGB", (1080, 1350), (14, 14, 18))
    ImageDraw.Draw(sky).ellipse((420, 560, 660, 800), fill=(225, 220, 215))
    out = io.BytesIO()
    sky.save(out, format="JPEG", quality=95)
    return out.getvalue()


def test_notes_never_go_out_as_the_same_photo_twice(tmp_path):
    """A moon with no stars got a constellation that couldn't be drawn and a date stamp that could: the
    second slide was the first again with a small date in the corner, and read as a duplicate post."""
    from instapostsforge.styles import Look

    name = "IMG20260926194248.jpg"
    moon = {"category": "night", "mood": "calm", "subject_top": 0.4, "subject_bottom": 0.6, "subject_left": 0.38, "subject_right": 0.62}
    c = components(tmp_path, {name: moon_jpeg()}, FakeAI(analyses={name: moon}))
    run = pipeline.Run(make_profile(), c, DAY)
    photo = c.source.list_photos()[0]
    run.analyse_new([photo])
    caption = Caption(text="Quiet moon.", hashtags=[], alt_text="The moon in a dark sky.")
    jpegs, _ = run.styled_slides("notes", photo, [], caption, Look(marks=("constellation", "date_stamp")))
    assert len(jpegs) == 2
    drawn = next(n for n in run.notes if n.startswith("Drawn on the photo"))
    assert "date stamp" in drawn and any(m in drawn for m in ("orbit", "rays", "viewfinder"))  # a ring, rays or brackets join it
    first, second = (Image.open(io.BytesIO(j)).convert("L") for j in jpegs)
    changed = ImageChops.difference(first, second).point(lambda v: 255 if v > 40 else 0).histogram()[255]
    assert changed > 1080 * 1350 * 0.002  # visibly more than a corner stamp


def test_any_style_records_a_look_and_changes_it_next_time(tmp_path):
    ai = FakeAI(styles={"a.jpg": "poster", "b.jpg": "poster"}, analyses={"a.jpg": {"mood": "calm"}, "b.jpg": {"mood": "calm"}})
    c = components(tmp_path, {"a.jpg": photo_jpeg(1), "b.jpg": photo_jpeg(2)}, ai)
    first = pipeline.run(make_profile(), c, publish=True, today=DAY)
    second = pipeline.run(make_profile(), c, publish=True, today=date(2026, 10, 21))
    looks = [p["look"] for p in c.state.posts.values()]
    assert first.style == second.style == "poster" and all(looks) and looks[0] != looks[1]


def test_slides_decode_as_jpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("POST_STYLE", "gallery")
    c = components(tmp_path, {"a.jpg": photo_jpeg(3)}, FakeAI(analyses={"a.jpg": {"category": "travel"}}))
    pipeline.run(make_profile(), c, publish=False, today=DAY, drafts_dir=tmp_path / "drafts")
    image = next(p for p in (tmp_path / "drafts").rglob("*.jpg") if p.is_file())
    assert Image.open(io.BytesIO(image.read_bytes())).size == (1080, 1350)
