from datetime import date, datetime, timezone

from instapostsforge.models import Photo
from instapostsforge.selection import pick_next


def photo(pid, day):
    return Photo(id=pid, name=f"{pid}.jpg", created_at=datetime(2026, 1, day, tzinfo=timezone.utc))


PHOTOS = [photo("c", 3), photo("a", 1), photo("b", 2)]


def test_ordered_picks_oldest_unposted():
    assert pick_next(PHOTOS, set(), {}).id == "a"
    assert pick_next(PHOTOS, {"a"}, {}).id == "b"


def test_returns_none_when_everything_is_posted():
    assert pick_next(PHOTOS, {"a", "b", "c"}, {}) is None


def test_random_is_stable_within_a_day():
    settings = {"strategy": "random"}
    first = pick_next(PHOTOS, set(), settings, today=date(2026, 10, 25))
    again = pick_next(PHOTOS, set(), settings, today=date(2026, 10, 25))
    assert first == again
