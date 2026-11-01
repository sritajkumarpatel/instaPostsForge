"""The pop-out poster's cut-out: model plumbing, mask clean-up and the checks, with no model or network."""

import hashlib
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from instapostsforge import cli
from instapostsforge.imaging import Focus
from instapostsforge.imaging import cutout as cut
from fakes import FakeMaskMaker

PHOTO = Image.new("RGB", (600, 800), (90, 120, 80))
BOX = Focus(top=0.2, bottom=0.8, left=0.35, right=0.65)


def mask_of(*boxes, size=(600, 800), fill=255):
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for left, top, right, bottom in boxes:
        draw.rectangle((left * size[0], top * size[1], right * size[0] - 1, bottom * size[1] - 1), fill=fill)
    return mask


def check(*boxes, focus=BOX, fill=255, **settings):
    raw = mask_of(*boxes, fill=fill)
    return cut.check_mask(raw, cut.clean_mask(raw), focus, settings)


# --- the model file -----------------------------------------------------------------


def _pin(monkeypatch, content: bytes):
    model = cut.MODELS["isnet"]
    pinned = cut.Model(**{**model.__dict__, "sha256": hashlib.sha256(content).hexdigest()})
    monkeypatch.setitem(cut.MODELS, "isnet", pinned)
    return pinned


def test_model_is_downloaded_once_and_checked(tmp_path, monkeypatch):
    model = _pin(monkeypatch, b"weights")
    fetched = []

    def fetch(url, dest):
        fetched.append(url)
        dest.write_bytes(b"weights")

    path = cut.model_path("isnet", tmp_path, fetch)
    assert path == tmp_path / model.file and path.read_bytes() == b"weights"
    assert cut.model_path("isnet", tmp_path, fetch) == path
    assert fetched == [model.url]  # the cached copy is reused once it matches


def test_a_model_that_doesnt_match_its_pin_is_never_used(tmp_path, monkeypatch):
    model = _pin(monkeypatch, b"weights")
    with pytest.raises(cut.CutoutUnavailable, match="SHA-256"):
        cut.model_path("isnet", tmp_path, lambda url, dest: dest.write_bytes(b"tampered"))
    assert not (tmp_path / model.file).exists() and not list(tmp_path.glob("*.part"))


def test_a_failed_download_is_reported_without_details(tmp_path, monkeypatch):
    _pin(monkeypatch, b"weights")

    def fetch(url, dest):
        dest.write_bytes(b"half")
        raise ConnectionError("https://secret.example/token=abc")

    with pytest.raises(cut.CutoutUnavailable) as err:
        cut.model_path("isnet", tmp_path, fetch)
    assert "ConnectionError" in str(err.value) and "secret" not in str(err.value)
    assert not list(tmp_path.glob("*.part"))


def test_every_model_is_pinned():
    for model in cut.MODELS.values():
        assert len(model.sha256) == 64 and model.url.startswith("https://")


def test_settings_check_the_model_name(monkeypatch):
    assert cut.settings_from({})["model"] == "birefnet-lite"
    assert cut.settings_from({"style": {"popout": {"model": "isnet", "min_area": 0.1}}})["min_area"] == 0.1
    monkeypatch.setenv("POPOUT_MODEL", "isnet")
    assert cut.settings_from({})["model"] == "isnet"
    monkeypatch.setenv("POPOUT_MODEL", "nope")
    with pytest.raises(cut.CutoutUnavailable):
        cut.settings_from({})


def test_the_model_reads_a_normalised_square_and_its_answer_becomes_a_photo_sized_mask():
    model = cut.MODELS["isnet"]
    data = cut.model_input(model, PHOTO)
    assert data.shape == (1, 3, 1024, 1024) and data.dtype == np.float32
    prediction = np.zeros((1, 64, 64), dtype=np.float32)
    prediction[0, 16:48, 16:48] = 1
    mask = cut.to_mask(prediction, model, PHOTO.size)
    assert mask.size == PHOTO.size and mask.mode == "L"
    assert mask.getpixel((300, 400)) == 255 and mask.getpixel((10, 10)) == 0
    logits = cut.to_mask(prediction * 20 - 10, cut.MODELS["birefnet-lite"], PHOTO.size)  # BiRefNet answers in logits
    assert logits.getpixel((300, 400)) == 255 and logits.getpixel((10, 10)) == 0


# --- clean-up -------------------------------------------------------------------------


def test_components_are_labelled_largest_first():
    grid = np.zeros((10, 10), dtype=bool)
    grid[0:2, 0:2] = True
    grid[5:10, 5:10] = True
    labels, sizes = cut.components(grid)
    assert sizes == [25, 4] and labels[7, 7] == 1 and labels[0, 0] == 2


def test_clean_up_drops_specks_and_faint_leftovers_but_keeps_real_gaps():
    raw = mask_of((0.3, 0.2, 0.7, 0.8))
    draw = ImageDraw.Draw(raw)
    draw.rectangle((20, 20, 24, 24), fill=255)  # a speck
    draw.rectangle((40, 600, 140, 700), fill=90)  # a faint ghost
    draw.rectangle((380, 560, 382, 562), fill=0)  # a pinhole
    draw.rectangle((330, 250, 341, 261), fill=0)  # a small hole, found on the smaller copy
    draw.rectangle((240, 300, 300, 420), fill=0)  # a real gap, like between a leg and a jeep
    cleaned = cut.clean_mask(raw)
    assert cleaned.getpixel((335, 255)) == 255
    assert cleaned.getpixel((22, 22)) == 0
    assert cleaned.getpixel((90, 650)) == 0
    assert cleaned.getpixel((381, 561)) == 255
    assert cleaned.getpixel((270, 360)) == 0
    assert cleaned.getpixel((400, 500)) == 255


# --- checks ---------------------------------------------------------------------------


def test_one_clear_subject_with_room_can_pop_out():
    result = check((0.35, 0.2, 0.65, 0.8))
    assert result.ok and result.reasons == []
    assert result.variants == ["step_down", "step_left", "step_right", "break_frame"]
    assert result.summary().startswith("Pop-out possible")


@pytest.mark.parametrize(
    ("boxes", "focus", "reason"),
    [
        ([(0.45, 0.45, 0.55, 0.55)], Focus(0.4, 0.6, 0.4, 0.6), "too small"),
        ([(0.05, 0.05, 0.95, 0.9)], Focus(0.05, 0.9, 0.05, 0.95), "fills too much"),
        ([(0.1, 0.2, 0.4, 0.8), (0.6, 0.2, 0.9, 0.8)], Focus(0.2, 0.8, 0.1, 0.9), "several pieces"),
        ([(0.05, 0.2, 0.3, 0.8)], Focus(0.2, 0.8, 0.65, 0.95), "isn't the subject"),
        ([(0.0, 0.2, 1.0, 1.0)], Focus(0.2, 1.0, 0.0, 1.0), "no room"),
        ([(0.35, 0.2, 0.65, 0.8)], None, "no subject box"),
    ],
)
def test_the_pop_out_is_skipped_with_a_reason(boxes, focus, reason):
    result = check(*boxes, focus=focus)
    assert not result.ok and result.variants == []
    assert any(reason in r for r in result.reasons), result.reasons


def test_unsure_edges_are_skipped():
    raw = Image.new("L", (600, 800), 0)
    ImageDraw.Draw(raw).rectangle((210, 160, 390, 640), fill=255)
    ImageDraw.Draw(raw).rectangle((150, 100, 450, 700), outline=150, width=40)  # a wide half-sure halo
    result = cut.check_mask(raw, cut.clean_mask(raw), BOX)
    assert result.stats["soft_edge"] > 0.12 and any("unsure" in r for r in result.reasons)


def test_an_empty_mask_is_skipped():
    result = check(focus=BOX)
    assert not result.ok and "the model found no subject" in result.reasons


def test_variants_follow_the_room_in_the_photo():
    settings = cut.DEFAULTS
    assert cut.fitting_variants({"top": 0.1, "bottom": 0.0, "left": 0.1, "right": 0.1}, settings) == []
    assert cut.fitting_variants({"top": 0.0, "bottom": 0.1, "left": 0.0, "right": 0.0}, settings) == ["step_down"]
    assert cut.fitting_variants({"top": 0.0, "bottom": 0.03, "left": 0.0, "right": 0.05}, settings) == ["step_right"]


def test_the_check_can_skip_the_box_for_a_loose_file():
    raw = mask_of((0.35, 0.2, 0.65, 0.8))
    assert cut.check_mask(raw, cut.clean_mask(raw), None, need_box=False).ok


def test_stats_are_plain_numbers():
    json.dumps(check((0.35, 0.2, 0.65, 0.8)).stats)


# --- end to end ---------------------------------------------------------------------


def test_cut_out_never_changes_the_photo():
    photo = PHOTO.copy()
    result = cut.cut_out(photo, BOX, cut.settings_from({}), FakeMaskMaker())
    assert result.check.ok and photo.tobytes() == PHOTO.tobytes()
    subject = cut.subject_only(photo, result.mask)
    assert subject.getpixel((300, 400)) == (*PHOTO.getpixel((300, 400)), 255)
    assert subject.getpixel((5, 5))[3] == 0


def test_cut_out_with_an_unsure_model_is_skipped():
    result = cut.cut_out(PHOTO, BOX, cut.settings_from({}), FakeMaskMaker(soft=False, shapes=()))
    assert not result.check.ok


def test_forge_cutout_writes_the_mask_and_report(tmp_path, monkeypatch):
    image = tmp_path / "jeep.jpg"
    PHOTO.save(image)
    monkeypatch.setattr(cut, "MaskMaker", lambda name: FakeMaskMaker())
    assert cli.main(["cutout", "-p", "main", "--file", str(image), "--drafts", str(tmp_path / "drafts")]) == 0
    out = tmp_path / "drafts" / "cutout-jeep"
    report = json.loads((out / "check.json").read_text())
    assert report["ok"] and report["variants"][0] == "step_down"
    assert Image.open(out / "mask.png").mode == "L" and Image.open(out / "subject.png").mode == "RGBA"


def test_forge_cutout_explains_a_missing_model(tmp_path, monkeypatch, capsys):
    image = tmp_path / "jeep.jpg"
    PHOTO.save(image)

    def missing(name):
        raise cut.CutoutUnavailable("Could not download the isnet cut-out model (ConnectionError).")

    monkeypatch.setattr(cut, "MaskMaker", missing)
    assert cli.main(["cutout", "-p", "main", "--file", str(image)]) == 2
    assert "Could not download" in capsys.readouterr().err
