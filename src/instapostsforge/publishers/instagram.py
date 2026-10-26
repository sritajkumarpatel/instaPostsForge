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


# What to do about the errors Meta returns most often, by error code.
# https://developers.facebook.com/docs/graph-api/guides/error-handling
_ERROR_HINTS = {
    190: "The access token has expired or was revoked. Generate a new token in the Meta app dashboard "
    "(Instagram > API setup with Instagram login) and save it as the IG_..._ACCESS_TOKEN secret.",
    200: "Meta refused API access for this app or account. Check the app's Dashboard and Alerts at "
    "developers.facebook.com for a restriction or a required check, make sure your Instagram account is "
    "still a tester of the app and is a Professional account, then generate a new token.",
    10: "The token is missing a permission. Generate a new token with instagram_business_content_publish "
    "and instagram_business_manage_insights.",
    4: "Rate limited by Meta. Wait an hour and run again.",
    17: "Rate limited by Meta. Wait an hour and run again.",
    32: "Rate limited by Meta. Wait an hour and run again.",
    613: "Rate limited by Meta. Wait an hour and run again.",
}


def _api_error(what: str, error: dict, fallback: str) -> PublishError:
    message = error.get("message") or fallback
    code = error.get("code")
    hint = _ERROR_HINTS.get(code) if isinstance(code, int) else None
    if not hint and "access blocked" in message.lower():
        hint = _ERROR_HINTS[200]
    detail = f" (code {code})" if code is not None else ""
    return PublishError(f"{what} failed: {message}{detail}" + (f" {hint}" if hint else ""))


def _network_error(what: str, err: requests.RequestException) -> PublishError:
    # requests puts the full URL, access token included, in its messages; keep it out of logs.
    return PublishError(f"{what} failed: could not reach Instagram ({type(err).__name__}).")


class InstagramPublisher(Publisher):
    def __init__(self, settings: dict, session: requests.Session | None = None, sleep=time.sleep):
        self.token = env_value(settings.get("access_token_env"))
        self.base = f"{GRAPH_HOST}/{settings.get('api_version', 'v23.0')}"
        self.http = session or requests.Session()
        self.sleep = sleep
        self._user_id = env_value(settings.get("user_id_env"), required=False)

    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        try:
            response = self.http.request(method, f"{self.base}/{path}", params=params, timeout=60)
        except requests.RequestException as err:
            raise _network_error(f"Instagram API {method} {path}", err) from None
        data = response.json() if response.content else {}
        if response.status_code >= 400 or "error" in data:
            raise _api_error(f"Instagram API {method} {path}", data.get("error") or {}, response.text)
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

    def publish_carousel(self, image_urls: list[str], caption: str, alt_texts: list[str | None] | None = None) -> str:
        """One container per photo, then a carousel container that holds them, then publish.

        Docs: https://developers.facebook.com/docs/instagram-platform/content-publishing#carousel-posts"""
        if not 2 <= len(image_urls) <= 10:
            raise PublishError(f"A multi-photo post needs 2 to 10 photos, not {len(image_urls)}.")
        alt_texts = list(alt_texts or [])
        user_id = self.user_id  # an account or token problem shows here, before any photo is sent
        children = []
        for i, url in enumerate(image_urls):
            params = {"image_url": url, "is_carousel_item": "true"}
            alt = alt_texts[i] if i < len(alt_texts) else None
            try:
                child = self._call("POST", f"{user_id}/media", **params, **({"alt_text": alt} if alt else {}))
            except PublishError as err:
                if not alt or "alt" not in str(err).lower():
                    raise
                child = self._call("POST", f"{user_id}/media", **params)  # alt text not accepted on carousel items
            self._wait_until_ready(child["id"])
            children.append(child["id"])
        container_id = self._call(
            "POST", f"{user_id}/media", media_type="CAROUSEL", children=",".join(children), caption=caption
        )["id"]
        self._wait_until_ready(container_id)
        return self._call("POST", f"{user_id}/media_publish", creation_id=container_id)["id"]

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
    try:
        response = http.get(
            f"{GRAPH_HOST}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=30,
        )
    except requests.RequestException as err:
        raise _network_error("Token refresh", err) from None
    data = response.json()
    if response.status_code >= 400 or "error" in data:
        raise _api_error("Token refresh", data.get("error") or {}, response.text)
    return data
