"""Reads photos from a Cloudflare R2 bucket through its S3-compatible API.

The bucket stays private: a read-only R2 API token lists and downloads the photos under an
optional prefix (a folder inside the bucket). Each photo's ID is its object key.
"""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath

from ..config import ConfigError, env_value
from ..models import Photo
from .base import PhotoSource
from .local_folder import IMAGE_EXTENSIONS


def _env(settings: dict, key: str) -> str:
    name = settings.get(key)
    value = env_value(name, required=False)
    if not value:
        raise ConfigError(f"The photo source is r2, but {name or 'source.r2.' + key} is empty. Add it to .env, or as a repository secret.")
    return value


class R2Source(PhotoSource):
    def __init__(self, settings: dict, client=None):
        self.bucket = settings.get("bucket") or _env(settings, "bucket_env")
        self.prefix = str(settings.get("prefix") or "").lstrip("/")
        if self.prefix and not self.prefix.endswith("/"):
            self.prefix += "/"
        self.client = client or self._connect(settings)

    @staticmethod
    def _connect(settings: dict):
        import boto3
        from botocore.config import Config

        account = _env(settings, "account_id_env")
        return boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=_env(settings, "access_key_id_env"),
            aws_secret_access_key=_env(settings, "secret_access_key_env"),
            region_name="auto",
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    def list_photos(self) -> list[Photo]:
        photos: list[Photo] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                name = PurePosixPath(key).name
                if key.endswith("/") or PurePosixPath(name).suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                photos.append(Photo(id=key, name=name, created_at=item.get("LastModified"), mime_type=mimetypes.guess_type(name)[0]))
        return sorted(photos, key=lambda p: p.id)

    def download(self, photo: Photo) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=photo.id)["Body"].read()
