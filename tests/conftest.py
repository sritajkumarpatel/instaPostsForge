import io

import pytest
from PIL import Image


def make_jpeg(width: int, height: int, color=(120, 160, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def jpeg_factory():
    return make_jpeg
