"""Publishes to Instagram through the official Instagram API with Instagram Login.

Docs: https://developers.facebook.com/documentation/instagram-platform/content-publishing
No password is ever used; the account owner grants a long-lived access token.
"""

from __future__ import annotations

import time

import requests

from ..config import env_value
from .base import Publisher

GRAPH_HOST = "https://graph.instagram.com"


class PublishError(Exception):
    pass


class InstagramPublisher(Publisher):
    def __init__(self, settings: dict, session: requests.Session | None = None, sleep=time.sleep):
        self.token = env_value(settings.get("access_token_env"))
        self.base = f"{GRAPH_HOST}/{settings.get('api_version', 'v23.0')}"
        self.http = session or requests.Session()
        self.sleep = sleep
        self._user_id = env_value(settings.get("user_id_env"), required=False)

    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        response = self.http.request(method, f"{self.base}/{path}", params=params, timeout=60)
        data = response.json() if response.content else {}
        if response.status_code >= 400 or "error" in data:
            message = data.get("error", {}).get("message", response.text)
            raise PublishError(f"Instagram API {method} {path} failed: {message}")
        return data

    @property
    def user_id(self) -> str:
        if not self._user_id:
            self._user_id = str(self._call("GET", "me", fields="user_id,username")["user_id"])
        return self._user_id

    def account(self) -> dict:
        return self._call("GET", "me", fields="user_id,username,account_type")

    def publishing_quota(self) -> dict:
        data = self._call("GET", f"{self.user_id}/content_publishing_limit", fields="quota_usage,config")
        return (data.get("data") or [{}])[0]

    def fetch_insights(self, media_id: str, metrics: str = "reach,likes,comments,saved,shares") -> dict:
        """Returns {metric: value} for one post. Needs the instagram_business_manage_insights permission."""
        data = self._call("GET", f"{media_id}/insights", metric=metrics)
        out = {}
        for item in data.get("data", []):
            values = item.get("values") or [{}]
            value = item.get("total_value", {}).get("value", values[0].get("value"))
            if value is not None:
                out[item["name"]] = value
        return out

    def publish_image(self, image_url: str, caption: str, alt_text: str | None = None) -> str:
        params = {"image_url": image_url, "caption": caption}
        if alt_text:
            params["alt_text"] = alt_text
        container_id = self._call("POST", f"{self.user_id}/media", **params)["id"]
        self._wait_until_ready(container_id)
        return self._call("POST", f"{self.user_id}/media_publish", creation_id=container_id)["id"]

    def _wait_until_ready(self, container_id: str, attempts: int = 10, delay: float = 5.0) -> None:
        for _ in range(attempts):
            status = self._call("GET", container_id, fields="status_code").get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram could not process the image (status {status}).")
            self.sleep(delay)
        raise PublishError("Instagram took too long to process the image.")


def refresh_long_lived_token(token: str, session: requests.Session | None = None) -> dict:
    """Extends a long-lived token by 60 days. Works on tokens at least 24 hours old."""
    http = session or requests.Session()
    response = http.get(
        f"{GRAPH_HOST}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=30,
    )
    data = response.json()
    if response.status_code >= 400 or "error" in data:
        raise PublishError(f"Token refresh failed: {data.get('error', {}).get('message', response.text)}")
    return data
