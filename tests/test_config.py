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
    assert profile.ai["model"] in profile.ai["pricing"]
