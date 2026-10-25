import pytest

from instapostsforge.publishers.instagram import InstagramPublisher, PublishError


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.content, self.text = data, status, b"x", str(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def request(self, method, url, params=None, timeout=None):
        self.calls.append((method, url.rsplit("/", 2)[-2:], dict(params)))
        return FakeResponse(self.replies.pop(0))


@pytest.fixture(autouse=True)
def token_env(monkeypatch):
    monkeypatch.setenv("IG_TOKEN", "tok")
    monkeypatch.setenv("IG_USER", "17841")


SETTINGS = {"access_token_env": "IG_TOKEN", "user_id_env": "IG_USER", "api_version": "v23.0"}


def test_publish_creates_container_waits_then_publishes():
    session = FakeSession([{"id": "c1"}, {"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}, {"id": "m1"}])
    publisher = InstagramPublisher(SETTINGS, session=session, sleep=lambda s: None)
    assert publisher.publish_image("https://x/img.jpg", "hello", "alt") == "m1"
    first = session.calls[0]
    assert first[0] == "POST" and first[1] == ["17841", "media"]
    assert first[2]["image_url"] == "https://x/img.jpg" and first[2]["access_token"] == "tok"
    assert session.calls[-1][1] == ["17841", "media_publish"]


def test_container_error_raises():
    session = FakeSession([{"id": "c1"}, {"status_code": "ERROR"}])
    publisher = InstagramPublisher(SETTINGS, session=session, sleep=lambda s: None)
    with pytest.raises(PublishError):
        publisher.publish_image("https://x/img.jpg", "hello")


def test_network_errors_never_show_the_token():
    import requests

    class Broken:
        def request(self, method, url, params=None, timeout=None):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}?access_token={params['access_token']}")

    publisher = InstagramPublisher(SETTINGS, session=Broken())
    with pytest.raises(PublishError) as info:
        publisher.account()
    assert "tok" not in str(info.value) and info.value.__cause__ is None


def test_publish_carousel_makes_children_then_the_carousel():
    replies = [{"id": "k1"}, {"status_code": "FINISHED"}, {"id": "k2"}, {"status_code": "FINISHED"}, {"id": "car"}, {"status_code": "FINISHED"}, {"id": "m9"}]
    session = FakeSession(replies)
    publisher = InstagramPublisher(SETTINGS, session=session, sleep=lambda s: None)
    assert publisher.publish_carousel(["https://x/1.jpg", "https://x/2.jpg"], "hello", ["one", ""]) == "m9"
    child1, child2 = session.calls[0][2], session.calls[2][2]
    assert child1["is_carousel_item"] == "true" and child1["alt_text"] == "one" and "caption" not in child1
    assert "alt_text" not in child2
    carousel = session.calls[4][2]
    assert carousel["media_type"] == "CAROUSEL" and carousel["children"] == "k1,k2" and carousel["caption"] == "hello"
    assert session.calls[-1][1] == ["17841", "media_publish"]


def test_carousel_child_retries_without_alt_text():
    replies = [{"error": {"message": "alt_text not supported"}}, {"id": "k1"}, {"status_code": "FINISHED"}, {"id": "k2"}, {"status_code": "FINISHED"}, {"id": "car"}, {"status_code": "FINISHED"}, {"id": "m1"}]
    session = FakeSession(replies)
    publisher = InstagramPublisher(SETTINGS, session=session, sleep=lambda s: None)
    assert publisher.publish_carousel(["https://x/1.jpg", "https://x/2.jpg"], "hi", ["one", "two"]) == "m1"
    assert "alt_text" not in session.calls[1][2]


def test_carousel_needs_two_to_ten_photos():
    publisher = InstagramPublisher(SETTINGS, session=FakeSession([]), sleep=lambda s: None)
    with pytest.raises(PublishError, match="2 to 10"):
        publisher.publish_carousel(["https://x/1.jpg"], "hi")
