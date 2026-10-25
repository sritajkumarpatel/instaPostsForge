"""Hosts the day's photo in a public GitHub repo for the minute it takes to post.

The JPEG is committed to a separate branch (default: media), Instagram is given its
raw.githubusercontent.com link, and the file is deleted once the post is published.
Needs a public repo and a token that can write to it; in GitHub Actions the built-in
GITHUB_TOKEN and GITHUB_REPOSITORY are used.
"""

from __future__ import annotations

import base64
import logging
import time

import requests

from ..config import ConfigError, env_value
from .base import ImageHost

API = "https://api.github.com"
log = logging.getLogger(__name__)


class GitHubHost(ImageHost):
    def __init__(self, settings: dict, session: requests.Session | None = None, sleep=time.sleep, head=requests.head):
        self.repo = env_value(settings.get("repository_env", "GITHUB_REPOSITORY"))
        self.branch = settings.get("branch", "media")
        self.http = session or requests.Session()
        self.http.headers.update(
            {
                "Authorization": f"Bearer {env_value(settings.get('token_env', 'GITHUB_TOKEN'))}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        self.sleep = sleep
        self.head = head
        self._shas: dict[str, str] = {}
        self._paths: dict[str, str] = {}
        self._ready = False

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        response = self.http.request(method, f"{API}/repos/{self.repo}{path}", timeout=30, **kwargs)
        if response.status_code >= 400 and response.status_code != 404:
            raise RuntimeError(f"GitHub API {method} {path} failed ({response.status_code}): {response.text[:300]}")
        return response

    def _prepare(self) -> None:
        if self._ready:
            return
        repo = self._request("GET", "").json()
        if repo.get("private"):
            raise ConfigError(
                f"{self.repo} is private, so Instagram cannot download from it. Make the repo public "
                "or choose another hosting type."
            )
        if self._request("GET", f"/branches/{self.branch}").status_code == 404:
            default = repo["default_branch"]
            sha = self._request("GET", f"/git/ref/heads/{default}").json()["object"]["sha"]
            self._request("POST", "/git/refs", json={"ref": f"refs/heads/{self.branch}", "sha": sha})
        self._ready = True

    def upload(self, key: str, jpeg: bytes) -> str:
        self._prepare()
        path = f"media/{int(time.time())}-{key}"
        response = self._request(
            "PUT",
            f"/contents/{path}",
            json={"message": f"Stage {key} for posting", "content": base64.b64encode(jpeg).decode(), "branch": self.branch},
        )
        self._shas[key] = response.json()["content"]["sha"]
        self._paths[key] = path
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{path}"
        self._wait_until_public(url)
        return url

    def _wait_until_public(self, url: str, attempts: int = 10) -> None:
        for _ in range(attempts):
            if self.head(url, timeout=15).status_code == 200:
                return
            self.sleep(3)
        raise RuntimeError(f"The staged image never became reachable at {url}")

    def delete(self, key: str) -> None:
        path = self._paths.get(key)
        if not path:
            return
        self._request(
            "DELETE",
            f"/contents/{path}",
            json={"message": f"Remove {key} after posting", "sha": self._shas[key], "branch": self.branch},
        )
