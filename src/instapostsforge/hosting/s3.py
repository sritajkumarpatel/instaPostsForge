"""S3-compatible storage (Cloudflare R2, AWS S3, Backblaze B2) with short-lived signed URLs."""

from __future__ import annotations

from ..config import env_value
from .base import ImageHost


class S3Host(ImageHost):
    def __init__(self, settings: dict):
        import boto3

        self.bucket = env_value(settings.get("bucket_env"))
        self.expiry = int(settings.get("url_expiry_seconds", 3600))
        self.client = boto3.client(
            "s3",
            endpoint_url=env_value(settings.get("endpoint_url_env"), required=False),
            aws_access_key_id=env_value(settings.get("access_key_id_env")),
            aws_secret_access_key=env_value(settings.get("secret_access_key_env")),
            region_name=env_value(settings.get("region_env"), required=False) or "auto",
        )

    def upload(self, key: str, jpeg: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=jpeg, ContentType="image/jpeg")
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.expiry
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
