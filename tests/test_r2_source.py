"""Cloudflare R2 as a photo source, and the PHOTO_SOURCE switch. A fake S3 client stands in for R2."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from instapostsforge.config import ConfigError, load_profile
from instapostsforge.sources import build_source, source_type
from instapostsforge.sources.r2 import R2Source

R2_ENV = ("PHOTO_SOURCE", "R2_BUCKET_MAIN", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


@pytest.fixture(autouse=True)
def no_source_env(monkeypatch):
    for name in R2_ENV:
        monkeypatch.delenv(name, raising=False)


class FakeS3:
    def __init__(self, pages, bodies=None):
        self.pages, self.bodies, self.calls = pages, bodies or {}, []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class Paginator:
            def paginate(self, **kwargs):
                fake.calls.append(kwargs)
                return iter(fake.pages)

        return Paginator()

    def get_object(self, Bucket, Key):
        self.calls.append({"Bucket": Bucket, "Key": Key})
        return {"Body": SimpleNamespace(read=lambda: self.bodies[Key])}


WHEN = datetime(2026, 10, 29, 9, 30, tzinfo=timezone.utc)


def test_lists_images_under_the_prefix_across_pages():
    pages = [
        {"Contents": [{"Key": "photos/", "LastModified": WHEN}, {"Key": "photos/b.JPG", "LastModified": WHEN},
                      {"Key": "photos/notes.txt", "LastModified": WHEN}]},
        {"Contents": [{"Key": "photos/2026/a.heic", "LastModified": WHEN}]},
        {},
    ]
    s3 = FakeS3(pages)
    source = R2Source({"bucket": "my-photos", "prefix": "/photos"}, client=s3)
    photos = source.list_photos()
    assert [(p.id, p.name) for p in photos] == [("photos/2026/a.heic", "a.heic"), ("photos/b.JPG", "b.JPG")]
    assert photos[1].created_at == WHEN and photos[1].mime_type == "image/jpeg"
    assert s3.calls[0] == {"Bucket": "my-photos", "Prefix": "photos/"}


def test_download_reads_the_object_by_its_key():
    s3 = FakeS3([], bodies={"photos/b.jpg": b"\xff\xd8jpeg"})
    source = R2Source({"bucket": "my-photos"}, client=s3)
    photo = SimpleNamespace(id="photos/b.jpg")
    assert source.download(photo) == b"\xff\xd8jpeg"
    assert s3.calls[-1] == {"Bucket": "my-photos", "Key": "photos/b.jpg"}


def test_empty_prefix_lists_the_whole_bucket():
    s3 = FakeS3([{}])
    R2Source({"bucket": "b", "prefix": ""}, client=s3).list_photos()
    assert s3.calls[0]["Prefix"] == ""


def test_missing_r2_secret_names_it():
    settings = {"bucket_env": "R2_BUCKET_MAIN", "account_id_env": "R2_ACCOUNT_ID",
                "access_key_id_env": "R2_ACCESS_KEY_ID", "secret_access_key_env": "R2_SECRET_ACCESS_KEY"}
    with pytest.raises(ConfigError, match="The photo source is r2, but R2_BUCKET_MAIN is empty"):
        R2Source(settings)


def test_missing_r2_key_names_it(monkeypatch):
    monkeypatch.setenv("R2_BUCKET_MAIN", "b")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acc")
    settings = {"bucket_env": "R2_BUCKET_MAIN", "account_id_env": "R2_ACCOUNT_ID",
                "access_key_id_env": "R2_ACCESS_KEY_ID", "secret_access_key_env": "R2_SECRET_ACCESS_KEY"}
    with pytest.raises(ConfigError, match="R2_ACCESS_KEY_ID is empty"):
        R2Source(settings)


def test_real_client_points_at_the_account_endpoint(monkeypatch):
    for name, value in (("R2_BUCKET_MAIN", "b"), ("R2_ACCOUNT_ID", "acc123"), ("R2_ACCESS_KEY_ID", "id"), ("R2_SECRET_ACCESS_KEY", "secret")):
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(Path(__file__).parent.parent)
    monkeypatch.setenv("PHOTO_SOURCE", "r2")
    source = build_source(load_profile("main").source)
    assert isinstance(source, R2Source) and source.bucket == "b"
    assert source.client.meta.endpoint_url == "https://acc123.r2.cloudflarestorage.com"


def test_photo_source_overrides_config(monkeypatch):
    assert source_type({"type": "google_drive"}) == "google_drive"
    monkeypatch.setenv("PHOTO_SOURCE", " 'R2' ")
    assert source_type({"type": "google_drive"}) == "r2"
    monkeypatch.setenv("PHOTO_SOURCE", "dropbox")
    with pytest.raises(ConfigError, match="Unknown photo source 'dropbox'. Use google_drive or r2"):
        source_type({"type": "google_drive"})


def test_r2_without_its_block_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("PHOTO_SOURCE", "r2")
    with pytest.raises(ConfigError, match="no source.r2 block"):
        build_source({"type": "google_drive"})


def test_shipped_config_has_both_sources(monkeypatch):
    monkeypatch.chdir(Path(__file__).parent.parent)
    source = load_profile("main").source
    assert source["type"] == "google_drive"
    assert source["r2"] == {
        "account_id_env": "R2_ACCOUNT_ID", "access_key_id_env": "R2_ACCESS_KEY_ID",
        "secret_access_key_env": "R2_SECRET_ACCESS_KEY", "bucket_env": "R2_BUCKET_MAIN", "prefix": "",
    }


def test_forge_check_hides_r2_error_details(monkeypatch):
    from botocore.exceptions import ClientError

    from instapostsforge import cli, sources

    class Broken:
        def list_photos(self):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "bucket my-secret-bucket"}}, "ListObjectsV2")

    monkeypatch.chdir(Path(__file__).parent.parent)
    monkeypatch.setenv("PHOTO_SOURCE", "r2")
    monkeypatch.setattr(cli, "check_ai", lambda settings: "AI ok")
    monkeypatch.setattr(sources, "build_source", lambda settings: Broken())

    class Publisher:
        def __init__(self, settings):
            pass

        def account(self):
            return {"username": "me", "account_type": "CREATOR"}

        def publishing_quota(self):
            return "0/50"

    from instapostsforge import publishers

    monkeypatch.setattr(publishers, "InstagramPublisher", Publisher)
    with pytest.raises(ConfigError) as err:
        cli.cmd_check(SimpleNamespace(profile="main", config=Path("config/profiles.yaml")))
    assert "Cloudflare R2 refused the request: ClientError AccessDenied" in str(err.value)
    assert "my-secret-bucket" not in str(err.value)
