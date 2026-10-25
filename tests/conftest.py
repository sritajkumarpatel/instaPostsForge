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


def make_distinct_jpeg(seed: int, width: int = 400, height: int = 400) -> bytes:
    """A random-noise photo; different seeds give photos that are not near-duplicates."""
    import random

    rng = random.Random(seed)
    image = Image.new("L", (16, 16))
    image.putdata([rng.randrange(256) for _ in range(256)])
    out = io.BytesIO()
    image.resize((width, height)).convert("RGB").save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def distinct_jpeg():
    return make_distinct_jpeg
