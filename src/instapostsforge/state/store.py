"""Remembers which photos each profile has already posted, so none repeat."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_DIR = Path("state")


class PostLog:
    """One JSON file per profile at state/<profile>.json. It holds no secrets."""

    def __init__(self, profile: str, state_dir: Path = DEFAULT_STATE_DIR):
        self.path = state_dir / f"{profile}.json"
        self.data = {"posted": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("posted", {})

    def is_posted(self, photo_id: str) -> bool:
        return photo_id in self.data["posted"]

    def posted_ids(self) -> set[str]:
        return set(self.data["posted"])

    def recent_captions(self, limit: int = 10) -> list[str]:
        entries = sorted(self.data["posted"].values(), key=lambda e: e.get("posted_at", ""))
        return [e["caption"] for e in entries[-limit:] if e.get("caption")]

    def record(self, photo_id: str, photo_name: str, media_id: str, caption: str) -> None:
        self.data["posted"][photo_id] = {
            "name": photo_name,
            "media_id": media_id,
            "caption": caption,
            "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.path)
