import textwrap

import pytest

from instapostsforge.config import ConfigError, deep_merge, list_profiles, load_profile


def write_config(tmp_path, body):
    path = tmp_path / "profiles.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def test_deep_merge_keeps_unrelated_defaults():
    merged = deep_merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}})
    assert merged == {"a": {"x": 1, "y": 3}, "b": 1}


def test_profile_inherits_defaults_and_overrides(tmp_path):
    path = write_config(
        tmp_path,
        """
        defaults:
          ai: {language: English, hashtags: {min: 3, max: 5}}
        profiles:
          main:
            instagram: {access_token_env: IG_A}
            source: {type: local_folder, path: /tmp}
          travel:
            instagram: {access_token_env: IG_B}
            source: {type: google_drive, folder_url: abcdefghijkl}
            ai: {hashtags: {max: 4}}
        """,
    )
    assert list_profiles(path) == ["main", "travel"]
    travel = load_profile("travel", path)
    assert travel.ai == {"language": "English", "hashtags": {"min": 3, "max": 4}}
    assert travel.instagram["access_token_env"] == "IG_B"
    assert load_profile("main", path).ai["hashtags"]["max"] == 5


def test_unknown_profile_is_a_clear_error(tmp_path):
    path = write_config(tmp_path, "profiles:\n  main:\n    instagram: {a: 1}\n    source: {type: x}\n")
    with pytest.raises(ConfigError, match="Unknown profile 'nope'"):
        load_profile("nope", path)


def test_shipped_config_loads():
    profile = load_profile("main")
    assert profile.source["type"] == "google_drive"
    assert "folder_url" not in profile.source  # the link lives in a secret, not the public repo
    assert profile.ai["hashtags"]["max"] == 5
    assert profile.hosting["type"] == "github"
    assert set(profile.ai["agents"]) == {"analyst", "planner", "caption_writer", "hashtags", "reviewer"}
    assert set(profile.ai["models"]) == {"claude", "openai", "gemini"}
    assert all(model in profile.ai["pricing"] for model in profile.ai["models"].values())


def test_refresh_token_can_write_to_a_file(tmp_path, monkeypatch, capsys):
    from instapostsforge import cli
    from instapostsforge.publishers import instagram

    monkeypatch.setenv("IG_MAIN_ACCESS_TOKEN", "old")
    monkeypatch.setattr(instagram, "refresh_long_lived_token", lambda token: {"access_token": "secret-new", "expires_in": 5184000})
    out = tmp_path / "token"
    assert cli.main(["refresh-token", "-p", "main", "--token-file", str(out)]) == 0
    assert out.read_text() == "secret-new"
    assert "secret-new" not in capsys.readouterr().out


def test_drive_photo_metadata_is_parsed():
    from instapostsforge.sources.google_drive import parse_exif_time, parse_location

    assert parse_exif_time("2026:10:18 10:48:37").isoformat() == "2026-10-18T10:48:37"
    assert parse_exif_time("not a time") is None and parse_exif_time(None) is None
    assert parse_location({"latitude": 12.97, "longitude": 77.59, "altitude": 900}) == (12.97, 77.59)
    assert parse_location({"latitude": 0, "longitude": 0}) is None
    assert parse_location({}) is None and parse_location(None) is None


def test_check_reports_metadata_counts_without_locations():
    from instapostsforge.cli import metadata_report
    from instapostsforge.models import Photo

    photos = [
        Photo(id="1", name="IMG20261018104837.jpg"),
        Photo(id="2", name="x.jpg", location=(12.97, 77.59)),
        Photo(id="3", name="y.jpg"),
    ]
    report = metadata_report(photos)
    assert "1 of 3 have a capture time" in report and "1 of 3 have a GPS location" in report
    assert "12.97" not in report
