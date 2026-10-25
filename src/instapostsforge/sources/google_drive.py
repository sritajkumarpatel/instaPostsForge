"""Reads photos from a Google Drive folder through the Drive API."""

from __future__ import annotations

import io
import re
from datetime import datetime

from ..config import ConfigError, env_value
from ..models import Photo
from .base import PhotoSource

FOLDER_ID_PATTERNS = (
    re.compile(r"/folders/([A-Za-z0-9_-]+)"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]+)"),
)
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def parse_folder_id(folder_url: str) -> str:
    """Accepts a full share link or a bare folder ID."""
    for pattern in FOLDER_ID_PATTERNS:
        match = pattern.search(folder_url)
        if match:
            return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", folder_url):
        return folder_url
    raise ConfigError(f"Could not find a Drive folder ID in: {folder_url}")


class GoogleDriveSource(PhotoSource):
    def __init__(self, settings: dict):
        from googleapiclient.discovery import build

        self.folder_id = parse_folder_id(settings.get("folder_url") or settings.get("folder_id") or "")
        auth = settings.get("auth", "api_key")
        if auth == "api_key":
            self.service = build(
                "drive", "v3", developerKey=env_value(settings.get("api_key_env")), cache_discovery=False
            )
        elif auth == "service_account":
            from google.oauth2 import service_account

            key_file = env_value(settings.get("service_account_file_env"))
            credentials = service_account.Credentials.from_service_account_file(key_file, scopes=SCOPES)
            self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        else:
            raise ConfigError(f"Unknown Google Drive auth '{auth}'. Use api_key or service_account.")

    def list_photos(self) -> list[Photo]:
        photos: list[Photo] = []
        page_token = None
        query = f"'{self.folder_id}' in parents and mimeType contains 'image/' and trashed = false"
        while True:
            response = (
                self.service.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType, createdTime)",
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for item in response.get("files", []):
                created = item.get("createdTime")
                photos.append(
                    Photo(
                        id=item["id"],
                        name=item["name"],
                        created_at=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
                        mime_type=item.get("mimeType"),
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return photos

    def download(self, photo: Photo) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        buffer = io.BytesIO()
        request = self.service.files().get_media(fileId=photo.id, supportsAllDrives=True)
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()
