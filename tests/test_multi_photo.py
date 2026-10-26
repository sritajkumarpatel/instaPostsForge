"""Multi-photo (carousel) posts: grouping, the planner's choice, framing, publishing and recording."""

import io
from datetime import date, datetime

import pytest
from fakes import FakeAI
from PIL import Image
from test_pipeline import components, make_profile

from instapostsforge import pipeline
from instapostsforge.grouping import km_between, propose_sets
from instapostsforge.models import Photo
from instapostsforge.state import ProfileState
from instapostsforge.state.store import photo_key

MULTI = {"strategy": "ai_planner", "multi_photo": {"enabled": True, "max_photos": 5, "max_per_7_days": 3}}

# Names carry the capture time, as phone photos do: a morning outing of three and one evening photo.
OUTING = ["IMG20261018090000.jpg", "IMG20261018093000.jpg", "IMG20261018101500.jpg"]
EVENING = "IMG20261018200000.jpg"


@pytest.fixture(autouse=True)
def no_switch(monkeypatch):
    monkeypatch.delenv("MULTI_PHOTO", raising=False)


@pytest.fixture
def files(distinct_jpeg):
    return {name: distinct_jpeg(i) for i, name in enumerate([*OUTING, EVENING])}


def subjects(names):
    return {n: {"main_subject": f"thing{i}", "category": ["landscape", "animals", "macro", "sky"][i]} for i, n in enumerate(names)}


def photo(pid, hour=None, loc=None):
    return Photo(id=pid, name=pid, taken_at=datetime(2026, 10, 18, hour) if hour is not None else None, location=loc)


# --- grouping -------------------------------------------------------------------


def test_outings_split_on_time_gaps_and_gps_jumps():
    photos = [photo("a", 9, (12.80, 77.57)), photo("b", 10, (12.80, 77.58)), photo("c", 11, (13.50, 77.58)), photo("d", 20)]
    times = {p.id: p.taken_at for p in photos}
    sets = propose_sets(photos, times=times, place_names={}, quality={}, hours=3, km=2)
    assert [s.ids for s in sets] == [["a", "b"]]  # c is 78 km away; d is 9 hours later


def test_place_sets_join_different_days():
    photos = [photo("a", loc=(12.80, 77.577)), photo("b", loc=(12.801, 77.578)), photo("c"), photo("d"), photo("e")]
    sets = propose_sets(photos, times={}, place_names={"c": "mysuru palace", "d": "mysuru palace"}, quality={"b": 9})
    assert [(s.kind, s.ids) for s in sets] == [("place", ["b", "a"]), ("place", ["c", "d"])]  # best photo first
    assert km_between((12.80, 77.577), (12.801, 77.578)) < 0.2


# --- the switch -------------------------------------------------------------------


def test_multi_photo_switch_overrides_config(monkeypatch):
    profile = make_profile(selection=MULTI)
    assert pipeline.multi_photo_settings(profile)["enabled"]
    monkeypatch.setenv("MULTI_PHOTO", "false")
    assert not pipeline.multi_photo_settings(profile)["enabled"]
    monkeypatch.setenv("MULTI_PHOTO", "true")
    assert pipeline.multi_photo_settings(make_profile())["enabled"]
    monkeypatch.delenv("MULTI_PHOTO")
    assert not pipeline.multi_photo_settings(make_profile())["enabled"]  # off unless config or the switch turns it on


def test_switched_off_means_single_photos_only(tmp_path, files, monkeypatch):
    monkeypatch.setenv("MULTI_PHOTO", "off")
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:]}, analyses=subjects(OUTING))
    result = pipeline.run(make_profile(selection=MULTI), components(tmp_path, files, ai), publish=True)
    assert result.more_photos == [] and ai.calls["plan"][0]["photo_sets"] is None


# --- planning and posting -----------------------------------------------------------


def test_planner_sees_the_outing_as_a_set(tmp_path, files):
    ai = FakeAI(analyses=subjects(OUTING))
    pipeline.plan_days(make_profile(selection=MULTI), components(tmp_path, files, ai, publish=False), 1)
    sets = ai.calls["plan"][0]["photo_sets"]
    assert sets["max_photos_per_post"] == 5 and sets["multi_photo_posts_in_last_7"] == 0
    assert [sorted(s["photo_ids"]) for s in sets["sets"]] == [sorted(OUTING)]


def test_multi_photo_post_publishes_and_records_every_photo(tmp_path, files):
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: [OUTING[1], OUTING[2]]}, analyses=subjects(OUTING))
    c = components(tmp_path, files, ai)
    result = pipeline.run(make_profile(selection=MULTI), c, publish=True)

    assert [p.id for p in result.photos] == OUTING
    urls, caption, alts = c.publisher.posts[0]
    assert len(urls) == 3 and alts == ["A wet street at night.", "Photo 2 of the set.", "Photo 3 of the set."]
    assert sorted(c.host.deleted) == sorted(c.host.uploaded)
    assert len(ai.calls["caption"][0]["more_photos"]) == 2 and ai.reviewed_photo_counts == [3]
    assert all("IMG" not in key for key in c.host.uploaded)  # staged names are hashed, never Drive IDs

    state = ProfileState("test", tmp_path / "state")
    assert all(state.is_posted(pid) for pid in OUTING)
    assert state.posts[photo_key(OUTING[0])]["more_photos"] == [photo_key(OUTING[1]), photo_key(OUTING[2])]
    assert state.history()[0]["photo_count"] == 3

    nxt = pipeline.run(make_profile(selection=MULTI), components(tmp_path, files, FakeAI(analyses=subjects(OUTING))), publish=True)
    assert nxt.photo.id == EVENING  # none of the three can come back


def test_extras_outside_the_cover_set_are_dropped(tmp_path, files):
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: [EVENING, OUTING[0], "nope.jpg", OUTING[2]]}, analyses=subjects(OUTING))
    result = pipeline.run(make_profile(selection=MULTI), components(tmp_path, files, ai), publish=True)
    assert [p.id for p in result.more_photos] == [OUTING[2]]


def test_max_photos_is_enforced(tmp_path, files):
    profile = make_profile(selection={**MULTI, "multi_photo": {**MULTI["multi_photo"], "max_photos": 2}})
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:]}, analyses=subjects(OUTING))
    result = pipeline.run(profile, components(tmp_path, files, ai), publish=True)
    assert len(result.photos) == 2


def test_weekly_limit_turns_a_set_into_a_single_photo(tmp_path, distinct_jpeg):
    profile = make_profile(selection={**MULTI, "multi_photo": {**MULTI["multi_photo"], "max_per_7_days": 1}})
    names = [f"IMG202610{d:02d}0{h}0000.jpg" for d in (10, 11) for h in (8, 9)]
    files = {n: distinct_jpeg(i) for i, n in enumerate(names)}
    analyses = {n: {"main_subject": f"s{i}", "category": ["sky", "food", "city", "animals"][i]} for i, n in enumerate(names)}
    first = pipeline.run(profile, components(tmp_path, files, FakeAI(plan_order=[names[0]], extras={names[0]: [names[1]]}, analyses=analyses)), publish=True)
    assert len(first.photos) == 2
    ai = FakeAI(plan_order=[names[2]], extras={names[2]: [names[3]]}, analyses=analyses)
    second = pipeline.run(profile, components(tmp_path, files, ai), publish=True, today=date(2026, 10, 26))
    assert second.photos == [second.photo]


def test_preview_saves_every_photo_in_order(tmp_path, files):
    ai = FakeAI(plan_order=[OUTING[2]], extras={OUTING[2]: [OUTING[0]]}, analyses=subjects(OUTING))
    result = pipeline.run(make_profile(selection=MULTI), components(tmp_path, files, ai, publish=False), publish=False, drafts_dir=tmp_path / "d")
    out = tmp_path / "d" / "test"
    draft = next(out.iterdir())
    assert sorted(f.name for f in draft.glob("*.jpg")) == ["1.jpg", "2.jpg"]
    assert (draft / "alt_text.txt").read_text().count("\n") == 2 and not result.published


def test_subjects_of_every_photo_count_for_variety(tmp_path, files):
    analyses = subjects(OUTING)
    analyses[EVENING] = {"main_subject": analyses[OUTING[2]]["main_subject"], "category": "night"}
    pipeline.run(make_profile(selection=MULTI), components(tmp_path, files, FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: OUTING[1:]}, analyses=analyses)), publish=True)
    state = ProfileState("test", tmp_path / "state")
    assert set(state.history()[0]["all_subjects"]) == {"thing0", "thing1", "thing2"}
    run = pipeline.Run(make_profile(selection=MULTI), components(tmp_path, files, FakeAI(analyses=analyses), publish=False))
    evening = pipeline.Photo(id=EVENING, name=EVENING)
    spare = pipeline.Photo(id="spare.jpg", name="spare.jpg")
    run.c.state.save_analysis("spare.jpg", "spare.jpg", {"main_subject": "kite", "category": "abstract"})
    order = [(evening, pipeline.PlanItem(EVENING, "", "")), (spare, pipeline.PlanItem("spare.jpg", "", ""))]
    assert run.keep_variety(order, 1)[0][0].id == "spare.jpg"  # the evening photo repeats the third photo's subject


def test_near_identical_frames_are_not_put_in_one_set(tmp_path, files):
    files[OUTING[1]] = files[OUTING[0]]  # a burst duplicate
    ai = FakeAI(analyses=subjects(OUTING))
    pipeline.plan_days(make_profile(selection=MULTI), components(tmp_path, files, ai, publish=False), 1)
    (only,) = ai.calls["plan"][0]["photo_sets"]["sets"]
    assert len(only["photo_ids"]) == 2 and OUTING[2] in only["photo_ids"]


# --- framing --------------------------------------------------------------------


def test_only_photos_of_one_shape_share_a_set(tmp_path, files, distinct_jpeg):
    files[OUTING[1]] = distinct_jpeg(11, width=600, height=400)  # a landscape shot in a square outing
    ai = FakeAI(plan_order=[OUTING[0]], extras={OUTING[0]: [OUTING[1], OUTING[2]]}, analyses=subjects(OUTING))
    c = components(tmp_path, files, ai)
    result = pipeline.run(make_profile(selection=MULTI), c, publish=True)
    (only,) = ai.calls["plan"][0]["photo_sets"]["sets"]
    assert OUTING[1] not in only["photo_ids"]
    assert [p.id for p in result.photos] == [OUTING[0], OUTING[2]]  # the landscape one is left for another day
    sizes = {Image.open(io.BytesIO(j)).size for j in c.host.uploaded.values()}
    assert sizes == {(400, 400)}  # posted exactly as they are: no border, no crop


def test_photos_analysed_before_shapes_are_measured_without_ai(tmp_path, files):
    c = components(tmp_path, files, FakeAI(), publish=False)
    for name in OUTING:
        c.state.save_analysis(name, name, {"category": "sky", "mood": "calm", "main_subject": name, "quality_score": 7})
    run = pipeline.Run(make_profile(selection=MULTI), c)
    run.analyse_new([pipeline.Photo(id=n, name=n) for n in OUTING])
    assert c.ai.calls["analyze"] == []
    assert all(c.state.analysis_for(n)["shape"] == 1.0 for n in OUTING)
    assert c.state.analysis_for(OUTING[0])["orientation"] == "square"


def test_mixed_shapes_are_never_posted_together(tmp_path, files, distinct_jpeg):
    files[OUTING[1]] = distinct_jpeg(11, width=600, height=400)
    run = pipeline.Run(make_profile(selection=MULTI), components(tmp_path, files, FakeAI(), publish=False))
    with pytest.raises(RuntimeError, match="one shape"):
        run.post_images(pipeline.Photo(id=OUTING[0], name=OUTING[0]), [pipeline.Photo(id=OUTING[1], name=OUTING[1])])


@pytest.mark.parametrize("value", ["False", " false ", "'false'", '"FALSE"', "0", "No", "off"])
def test_any_spelling_of_false_turns_multi_photo_off(monkeypatch, value):
    profile = make_profile(selection={"strategy": "ai_planner", "multi_photo": {"enabled": True}})
    monkeypatch.setenv("MULTI_PHOTO", value)
    settings = pipeline.multi_photo_settings(profile)
    assert settings["enabled"] is False and settings["switched_by"].startswith("MULTI_PHOTO=")
