"""Chooses which unposted photo goes out next."""

from __future__ import annotations

import random
from datetime import date, datetime, timezone

from .models import Photo

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def pick_next(photos: list[Photo], posted_ids: set[str], settings: dict, today: date | None = None) -> Photo | None:
    candidates = [p for p in photos if p.id not in posted_ids]
    if not candidates:
        return None
    strategy = settings.get("strategy", "ordered")
    if strategy == "random":
        # Seeded by date so a re-run on the same day picks the same photo.
        return random.Random((today or date.today()).isoformat()).choice(sorted(candidates, key=lambda p: p.id))
    return min(candidates, key=lambda p: (p.created_at or EPOCH, p.name))
