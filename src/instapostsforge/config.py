"""Loads config/profiles.yaml and resolves one profile into typed settings."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised when the profile file or the environment is missing something."""


DEFAULT_CONFIG_PATH = Path("config/profiles.yaml")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Returns base with override applied on top, merging nested dicts."""
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def env_value(name: str | None, *, required: bool = True) -> str | None:
    """Reads an environment variable whose name comes from the config."""
    if not name:
        if required:
            raise ConfigError("A required environment variable name is missing from the profile.")
        return None
    value = os.environ.get(name, "").strip()
    if not value and required:
        raise ConfigError(f"Environment variable {name} is not set. Add it to your .env file.")
    return value or None


@dataclass(frozen=True)
class Profile:
    """One Instagram account plus the photo source and settings that feed it."""

    name: str
    instagram: dict[str, Any]
    source: dict[str, Any]
    ai: dict[str, Any]
    image: dict[str, Any]
    selection: dict[str, Any]
    hosting: dict[str, Any]


def load_raw(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data.get("profiles"), dict) or not data["profiles"]:
        raise ConfigError(f"{path} has no profiles.")
    return data


def list_profiles(path: Path = DEFAULT_CONFIG_PATH) -> list[str]:
    return sorted(load_raw(path)["profiles"])


def load_profile(name: str, path: Path = DEFAULT_CONFIG_PATH) -> Profile:
    data = load_raw(path)
    if name not in data["profiles"]:
        known = ", ".join(sorted(data["profiles"]))
        raise ConfigError(f"Unknown profile '{name}'. Known profiles: {known}")
    merged = deep_merge(data.get("defaults") or {}, data["profiles"][name] or {})
    for section in ("instagram", "source"):
        if not merged.get(section):
            raise ConfigError(f"Profile '{name}' is missing its '{section}' section.")
    return Profile(
        name=name,
        instagram=merged.get("instagram", {}),
        source=merged.get("source", {}),
        ai=merged.get("ai", {}),
        image=merged.get("image", {}),
        selection=merged.get("selection", {}),
        hosting=merged.get("hosting", {}),
    )
