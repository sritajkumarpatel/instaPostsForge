import pytest

from instapostsforge.config import ConfigError
from instapostsforge.hosting.github import GitHubHost


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data, self.text = status, data or {}, str(data)

    def json(self):
        return self._data


class FakeGitHub:
    def __init__(self, private=False, branch_exists=True):
        self.headers = {}
        self.private, self.branch_exists = private, branch_exists
        self.calls = []

    def request(self, method, url, timeout=None, json=None):
        path = url.split("/repos/owner/repo", 1)[1]
        self.calls.append((method, path, json))
        if method == "GET" and path == "":
            return Resp(data={"private": self.private, "default_branch": "main"})
        if method == "GET" and path == "/branches/media":
            return Resp(200 if self.branch_exists else 404)
        if method == "GET" and path.startswith("/git/ref/"):
            return Resp(data={"object": {"sha": "abc"}})
        if method == "POST" and path == "/git/trees":
            return Resp(201, {"sha": "tree-sha"})
        if method == "POST" and path == "/git/commits":
            return Resp(201, {"sha": "commit-sha"})
        if method == "PUT":
            return Resp(201, {"content": {"sha": "file-sha"}})
        return Resp(201)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")


def host(gh):
    return GitHubHost({}, session=gh, sleep=lambda s: None, head=lambda url, timeout: Resp(200))


def test_upload_returns_raw_link_and_delete_uses_sha():
    gh = FakeGitHub()
    h = host(gh)
    url = h.upload("main-a.jpg", b"jpeg")
    assert url.startswith("https://raw.githubusercontent.com/owner/repo/media/media/") and url.endswith("main-a.jpg")
    h.delete("main-a.jpg")
    method, path, body = gh.calls[-1]
    assert method == "DELETE" and body["sha"] == "file-sha" and body["branch"] == "media"


def test_creates_empty_media_branch_when_missing():
    gh = FakeGitHub(branch_exists=False)
    host(gh).upload("x.jpg", b"j")
    commit = next(body for method, path, body in gh.calls if path == "/git/commits")
    assert commit["parents"] == [] and commit["tree"] == "tree-sha"  # no history: never a copy of main
    assert ("POST", "/git/refs", {"ref": "refs/heads/media", "sha": "commit-sha"}) in gh.calls


def test_private_repo_is_refused():
    with pytest.raises(ConfigError, match="private"):
        host(FakeGitHub(private=True)).upload("x.jpg", b"j")
