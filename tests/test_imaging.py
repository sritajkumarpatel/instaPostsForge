import io

from PIL import Image

from instapostsforge.imaging import prepare_image

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
