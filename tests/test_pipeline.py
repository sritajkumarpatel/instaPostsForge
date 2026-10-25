import pytest

from instapostsforge import pipeline
from instapostsforge.captions import CaptionWriter
from instapostsforge.config import Profile
from instapostsforge.hosting import ImageHost
from instapostsforge.models import Caption, Photo
from instapostsforge.publishers import Publisher
from instapostsforge.sources import PhotoSource
from instapostsforge.state import PostLog


class FakeSource(PhotoSource):
    def __init__(self, files):
        self.files = files

    def list_photos(self):
        return [Photo(id=name, name=name) for name in sorted(self.files)]

    def download(self, photo):
        return self.files[photo.id]


class FakeWriter(CaptionWriter):
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.calls = 0

    def write(self, image_jpeg, *, photo_name, recent_captions):
        self.calls += 1
        return self.drafts.pop(0)


class FakeHost(ImageHost):
    def __init__(self):
        self.uploaded, self.deleted = {}, []

    def upload(self, key, jpeg):
        self.uploaded[key] = jpeg
        return f"https://example.com/{key}"

    def delete(self, key):
        self.deleted.append(key)


class FakePublisher(Publisher):
    def __init__(self):
        self.posts = []

    def publish_image(self, image_url, caption, alt_text=None):
        self.posts.append((image_url, caption, alt_text))
        return "media-123"


GOOD = Caption(text="Quiet streets after rain.", hashtags=["#street", "#rain", "#citylights"], alt_text="A street.")
BAD = Caption(text="Too few tags.", hashtags=["#one"], alt_text="x")

PROFILE = Profile(
    name="test",
    instagram={},
    source={},
    captions={"hashtags": {"min": 3, "max": 5}},
    image={"min_ratio": 0.8, "max_ratio": 1.91},
    selection={},
    hosting={},
)


def test_preview_writes_draft_and_posts_nothing(tmp_path, jpeg_factory):
    components = pipeline.Components(
        source=FakeSource({"a.jpg": jpeg_factory(1000, 1000)}),
        writer=FakeWriter([GOOD]),
        post_log=PostLog("test", tmp_path / "state"),
    )
    result = pipeline.run(PROFILE, components, publish=False, drafts_dir=tmp_path / "drafts")
    assert not result.published
    assert any((tmp_path / "drafts").rglob("caption.txt"))
    assert not components.post_log.posted_ids()


def test_publish_posts_records_and_cleans_up(tmp_path, jpeg_factory):
    host, publisher = FakeHost(), FakePublisher()
    log = PostLog("test", tmp_path / "state")
    components = pipeline.Components(
        source=FakeSource({"a.jpg": jpeg_factory(900, 1600), "b.jpg": jpeg_factory(800, 800)}),
        writer=FakeWriter([BAD, GOOD]),  # first caption breaks a rule, the retry passes
        post_log=log,
        host=host,
        publisher=publisher,
    )
    result = pipeline.run(PROFILE, components, publish=True)
    assert result.published and result.media_id == "media-123"
    assert components.writer.calls == 2
    assert publisher.posts[0][1].endswith("#street #rain #citylights")
    assert host.deleted == list(host.uploaded)
    assert result.notes  # the tall photo was padded
    assert PostLog("test", tmp_path / "state").is_posted("a.jpg")


def test_nothing_left_to_post(tmp_path, jpeg_factory):
    log = PostLog("test", tmp_path / "state")
    log.record("a.jpg", "a.jpg", "m1", "c")
    components = pipeline.Components(
        source=FakeSource({"a.jpg": jpeg_factory(10, 10)}), writer=FakeWriter([]), post_log=log
    )
    with pytest.raises(pipeline.NothingToPost):
        pipeline.run(PROFILE, components, publish=False)
