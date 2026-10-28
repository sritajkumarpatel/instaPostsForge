import io

from PIL import Image

from instapostsforge.imaging import Focus, focus_from, prepare_image
from instapostsforge.imaging.processor import place

SETTINGS = {"keep_aspect_ratio": True, "min_ratio": 0.8, "max_ratio": 1.91, "max_width": 1440, "pad_color": "auto"}


def size_of(jpeg: bytes):
    return Image.open(io.BytesIO(jpeg)).size


def test_allowed_ratio_is_left_alone(jpeg_factory):
    prepared = prepare_image(jpeg_factory(1200, 900), SETTINGS)  # 4:3
    assert not prepared.padded
    assert size_of(prepared.jpeg) == (1200, 900)


def test_tall_phone_photo_is_padded_not_cropped(jpeg_factory):
    prepared = prepare_image(jpeg_factory(900, 1600), SETTINGS)  # 9:16
    assert prepared.padded
    w, h = size_of(prepared.jpeg)
    assert h == 1600 and abs(w / h - 0.8) < 0.01


def test_three_by_four_portrait_is_trimmed_to_four_by_five_without_a_border(jpeg_factory):
    prepared = prepare_image(jpeg_factory(900, 1200), SETTINGS)  # 3:4, the usual phone portrait
    assert prepared.cropped and not prepared.padded
    w, h = size_of(prepared.jpeg)
    assert w == 900 and abs(w / h - 0.8) < 0.01
    assert prepared.shape == 0.75


def test_slightly_too_wide_photo_is_trimmed(jpeg_factory):
    prepared = prepare_image(jpeg_factory(2000, 1000), SETTINGS)  # 2:1
    assert prepared.cropped and not prepared.padded
    w, h = size_of(prepared.jpeg)
    assert abs(w / h - 1.91) < 0.01


def test_very_wide_photo_is_padded(jpeg_factory):
    prepared = prepare_image(jpeg_factory(1400, 400), SETTINGS)
    assert prepared.padded
    w, h = size_of(prepared.jpeg)
    assert w == 1400 and abs(w / h - 1.91) < 0.01


def test_large_photo_is_scaled_down_keeping_ratio(jpeg_factory):
    prepared = prepare_image(jpeg_factory(4000, 3000), SETTINGS)
    assert size_of(prepared.jpeg) == (1440, 1080)
    assert max(size_of(prepared.preview_jpeg)) <= 1024


def test_png_is_converted_to_jpeg():
    out = io.BytesIO()
    Image.new("RGBA", (800, 800), (10, 20, 30, 128)).save(out, format="PNG")
    prepared = prepare_image(out.getvalue(), SETTINGS)
    assert Image.open(io.BytesIO(prepared.jpeg)).format == "JPEG"


def test_upload_keeps_colour_profile_and_full_colour_detail(jpeg_factory):
    from PIL import ImageCms, JpegImagePlugin

    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    out = io.BytesIO()
    Image.open(io.BytesIO(jpeg_factory(1200, 1500))).save(out, format="JPEG", icc_profile=profile)
    prepared = prepare_image(out.getvalue(), {**SETTINGS, "max_width": 1080})
    saved = Image.open(io.BytesIO(prepared.jpeg))
    assert saved.info.get("icc_profile") == profile
    assert JpegImagePlugin.get_sampling(saved) == 0
    assert saved.size == (1080, 1350)


# --- trimming around the main subject --------------------------------------------------


def banded_portrait(band_rows: range, width: int = 900, height: int = 1200) -> bytes:
    """A 3:4 portrait, blue except for a red band across the given rows."""
    image = Image.new("RGB", (width, height), (0, 0, 255))
    image.paste((255, 0, 0), (0, band_rows.start, width, band_rows.stop))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=95)
    return out.getvalue()


def is_red(rgb) -> bool:
    return rgb[0] > 200 and rgb[2] < 60


TRIM = {**SETTINGS, "max_crop": 0.1}


def test_a_trim_without_notes_is_centred(jpeg_factory):
    # 900 x 1200 keeps 1125 rows; centred, rows 0 to 36 go, so a red band in rows 0 to 29 is lost.
    prepared = prepare_image(banded_portrait(range(0, 30)), TRIM)
    assert prepared.cropped
    assert not is_red(Image.open(io.BytesIO(prepared.jpeg)).getpixel((450, 2)))


def test_the_trim_keeps_a_subject_at_the_top():
    prepared = prepare_image(banded_portrait(range(0, 30)), TRIM, Focus(0.0, 0.6, 0.1, 0.9))
    image = Image.open(io.BytesIO(prepared.jpeg))
    assert image.size == (900, 1125)
    assert is_red(image.getpixel((450, 2)))


def test_the_trim_keeps_a_subject_at_the_bottom():
    prepared = prepare_image(banded_portrait(range(1170, 1200)), TRIM, Focus(0.4, 1.0, 0.0, 1.0))
    image = Image.open(io.BytesIO(prepared.jpeg))
    assert is_red(image.getpixel((450, image.height - 3)))


def test_place_prefers_the_centre_and_centres_on_a_band_too_long_to_keep():
    assert place(1200, 1125, 0.2, 0.8) == 37  # fits anywhere: centred
    assert place(1200, 1125, 0.0, 0.6) == 0
    assert place(1200, 1125, 0.3, 1.0) == 75
    assert place(100, 80, 0.1, 0.95) == 12  # band 10..95 is longer than 80: centred on the band
    assert place(100, 80, None, None) == 10


def test_odd_subject_numbers_are_ignored():
    good = {"subject_top": 0.1, "subject_bottom": 0.9, "subject_left": 0.0, "subject_right": 1.0}
    assert focus_from(good) == Focus(0.1, 0.9, 0.0, 1.0)
    assert focus_from({**good, "subject_top": 0.95}) is None  # top below bottom
    assert focus_from({**good, "subject_right": 1.4}) is None
    assert focus_from({**good, "subject_left": None}) is None
    assert focus_from({**good, "subject_left": True}) is None
    assert focus_from(None) is None
