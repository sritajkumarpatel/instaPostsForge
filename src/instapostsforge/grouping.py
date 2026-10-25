"""Finds photos that could go out together as one multi-photo (carousel) post.

Plain code proposes the sets; the planner decides whether one of them makes a stronger post
than a single photo, and which 2 to 5 of its photos to use. Two kinds of set are proposed:

- an outing: photos taken one after another, with no gap longer than `hours`, and no jump
  longer than `km` between photos that both have GPS;
- a place: photos from different days taken within `km` of each other (by GPS), or with the
  same place name from the photo analyst.

GPS coordinates are only compared here, in memory. They are never written to state/.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from .models import Photo

MAX_SET_SIZE = 10  # the planner sees at most this many photos per set, the best first


@dataclass
class PhotoSet:
    set_id: str
    kind: str  # "outing" or "place"
    photos: list[Photo] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [p.id for p in self.photos]


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in kilometres."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _close(a: Photo, b: Photo, km: float) -> bool:
    return not (a.location and b.location) or km_between(a.location, b.location) <= km


def outings(photos: list[Photo], times: dict[str, datetime], *, hours: float, km: float) -> list[list[Photo]]:
    timed = sorted((p for p in photos if p.id in times), key=lambda p: (times[p.id], p.id))
    groups: list[list[Photo]] = []
    for photo in timed:
        last = groups[-1][-1] if groups else None
        if last and (times[photo.id] - times[last.id]).total_seconds() <= hours * 3600 and _close(last, photo, km):
            groups[-1].append(photo)
        else:
            groups.append([photo])
    return [g for g in groups if len(g) >= 2]


def places(photos: list[Photo], place_names: dict[str, str], *, km: float) -> list[list[Photo]]:
    groups: list[list[Photo]] = []
    for photo in sorted(photos, key=lambda p: p.id):
        name = place_names.get(photo.id, "")
        for group in groups:
            anchor = group[0]
            same_spot = photo.location and anchor.location and km_between(photo.location, anchor.location) <= km
            same_name = name and name == place_names.get(anchor.id, "")
            if same_spot or same_name:
                group.append(photo)
                break
        else:
            groups.append([photo])
    return [g for g in groups if len(g) >= 2]


def propose_sets(
    photos: list[Photo],
    *,
    times: dict[str, datetime],
    place_names: dict[str, str],
    quality: dict[str, int],
    hours: float = 3,
    km: float = 2,
) -> list[PhotoSet]:
    """Candidate sets of 2 or more photos, outings first. A place set that only repeats an outing is dropped."""
    found: list[PhotoSet] = []
    seen: set[frozenset[str]] = set()
    for kind, groups in (("outing", outings(photos, times, hours=hours, km=km)), ("place", places(photos, place_names, km=km))):
        for group in groups:
            best = sorted(group, key=lambda p: (-quality.get(p.id, 0), p.id))[:MAX_SET_SIZE]
            key = frozenset(p.id for p in best)
            if key in seen:
                continue
            seen.add(key)
            found.append(PhotoSet(f"set{len(found) + 1}", kind, best))
    return found
