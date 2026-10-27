"""Everything the workflow remembers for one profile, kept in state/<profile>/.

posts.json     every post, by its (cover) photo's key; a multi-photo post lists its other photos
               under "more_photos", so none of them is ever reused either
catalog.json   the photo analyst's notes per photo, so each photo is analysed once
insights.json  reach, likes, saves and shares per post, used by the planner and hashtag strategist

None of these files hold secrets; the workflow commits them back to the repo after each post.
Photos are keyed by a hash of their source file ID, never the ID itself: the repo is public,
and a Drive file ID is enough to download that photo from a link-shared folder.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_DIR = Path("state")


KEY_PREFIX = "h:"


def photo_key(photo_id: str) -> str:
    """A public, one-way key for a photo. Drive IDs are long random strings, so this can't be reversed."""
    if photo_id.startswith(KEY_PREFIX):
        return photo_id
    return KEY_PREFIX + hashlib.sha256(photo_id.encode("utf-8")).hexdigest()[:32]


def _hash_keys(data: dict) -> dict:
    return {photo_key(k): v for k, v in data.items()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProfileState:
    def __init__(self, profile: str, state_dir: Path = DEFAULT_STATE_DIR):
        self.dir = state_dir / profile
        # Older state files used raw IDs; they are re-keyed here and saved hashed on the next write.
        self.posts: dict = _hash_keys(self._load("posts.json"))
        self.catalog: dict = _hash_keys(self._load("catalog.json"))
        self.insights: dict = self._load("insights.json")

    def _load(self, name: str) -> dict:
        path = self.dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _save(self, name: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)

    # --- posts -------------------------------------------------------------

    def posted_ids(self) -> set[str]:
        """Keys of every photo posted, including the other photos of multi-photo posts."""
        return set(self.posts) | {k for post in self.posts.values() for k in post.get("more_photos", [])}

    def is_posted(self, photo_id: str) -> bool:
        return photo_key(photo_id) in self.posted_ids()

    def _subjects(self, key: str, post: dict) -> list[str]:
        keys = [key, *post.get("more_photos", [])]
        return [s for k in keys if (s := self.catalog.get(k, {}).get("analysis", {}).get("main_subject"))]

    def history(self, limit: int = 30) -> list[dict]:
        """Recent posts, newest first, joined with their analysis and results."""
        entries = sorted(self.posts.items(), key=lambda kv: kv[1].get("posted_at", ""), reverse=True)[:limit]
        out = []
        for key, post in entries:
            analysis = self.catalog.get(key, {}).get("analysis", {})
            out.append(
                {
                    "posted_at": post.get("posted_at"),
                    "category": analysis.get("category"),
                    "mood": analysis.get("mood"),
                    "subject": analysis.get("subject"),
                    "main_subject": analysis.get("main_subject"),
                    "all_subjects": self._subjects(key, post),
                    "photo_count": 1 + len(post.get("more_photos", [])),
                    "hashtags": post.get("hashtags", []),
                    "results": self.insights.get(post.get("media_id", ""), {}).get("metrics"),
                }
            )
        return out

    def recent_captions(self, limit: int = 10) -> list[str]:
        entries = sorted(self.posts.values(), key=lambda p: p.get("posted_at", ""), reverse=True)
        return [p["caption"] for p in entries[:limit] if p.get("caption")]

    def recent_tag_sets(self, limit: int = 10) -> list[list[str]]:
        entries = sorted(self.posts.values(), key=lambda p: p.get("posted_at", ""), reverse=True)
        return [p.get("hashtags", []) for p in entries[:limit]]

    def top_captions(self, limit: int = 3) -> list[str]:
        scored = [
            (self.insights[p["media_id"]]["metrics"].get("reach", 0), p["caption"])
            for p in self.posts.values()
            if p.get("media_id") in self.insights and p.get("caption")
        ]
        return [c for _, c in sorted(scored, reverse=True)[:limit]]

    def tag_stats(self) -> dict[str, dict]:
        """For each hashtag: how often it was used and the average reach of those posts."""
        reach = defaultdict(list)
        uses = defaultdict(int)
        for post in self.posts.values():
            metrics = self.insights.get(post.get("media_id", ""), {}).get("metrics", {})
            for tag in post.get("hashtags", []):
                uses[tag] += 1
                if "reach" in metrics:
                    reach[tag].append(metrics["reach"])
        return {
            tag: {"uses": uses[tag], "avg_reach": round(sum(reach[tag]) / len(reach[tag])) if reach[tag] else None}
            for tag in uses
        }

    def record_post(
        self,
        photo_id: str,
        *,
        photo_name: str,
        media_id: str,
        caption: str,
        hashtags: list[str],
        plan_reason: str,
        more_photo_ids: list[str] | None = None,
    ) -> None:
        entry = {
            "name": photo_name,
            "media_id": media_id,
            "caption": caption,
            "hashtags": hashtags,
            "plan_reason": plan_reason,
            "posted_at": _now(),
        }
        if more_photo_ids:
            entry["more_photos"] = [photo_key(i) for i in more_photo_ids]
        self.posts[photo_key(photo_id)] = entry
        self._save("posts.json", self.posts)

    def forget_posts(self) -> None:
        """Used by when_exhausted: recycle, to start the cycle over."""
        archive = self._load("posts_archive.json")
        archive.update({f"{k}@{v.get('posted_at')}": v for k, v in self.posts.items()})
        self._save("posts_archive.json", archive)
        self.posts = {}
        self._save("posts.json", self.posts)

    # --- catalog -----------------------------------------------------------

    def analysis_for(self, photo_id: str) -> dict | None:
        return self.catalog.get(photo_key(photo_id), {}).get("analysis")

    def fingerprint_for(self, photo_id: str) -> str | None:
        return self.catalog.get(photo_key(photo_id), {}).get("fingerprint")

    def save_analysis(self, photo_id: str, photo_name: str, analysis: dict, fingerprint: str | None = None) -> None:
        entry = {"name": photo_name, "analysis": analysis, "analysed_at": _now()}
        if fingerprint:
            entry["fingerprint"] = fingerprint
        self.catalog[photo_key(photo_id)] = entry
        self._save("catalog.json", self.catalog)

    def recent_fingerprints(self, days: int) -> list[str]:
        """Fingerprints of photos posted in the last `days` days."""
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        return [
            fp
            for key, post in self.posts.items()
            if post.get("posted_at") and datetime.fromisoformat(post["posted_at"]).timestamp() >= cutoff
            for k in (key, *post.get("more_photos", []))
            if (fp := self.catalog.get(k, {}).get("fingerprint"))
        ]

    # --- insights ----------------------------------------------------------

    def media_needing_insights(self, max_age_days: int = 30) -> list[str]:
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        out = []
        for post in self.posts.values():
            media_id, posted = post.get("media_id"), post.get("posted_at")
            if media_id and posted and datetime.fromisoformat(posted).timestamp() >= cutoff:
                out.append(media_id)
        return out

    def save_insights(self, media_id: str, metrics: dict) -> None:
        self.insights[media_id] = {"metrics": metrics, "fetched_at": _now()}
        self._save("insights.json", self.insights)


def reset_state(profile: str, *, keep_catalog: bool = False, state_dir: Path = DEFAULT_STATE_DIR) -> list[str]:
    """Forget a profile's posts, insights and usage log (and its photo analyses unless keep_catalog).

    For a fresh copy of the repo, or to start the posting history over. Returns what was cleared.
    """
    folder = state_dir / profile
    folder.mkdir(parents=True, exist_ok=True)
    names = ["posts.json", "insights.json"] + ([] if keep_catalog else ["catalog.json"])
    for name in names:
        (folder / name).write_text("{}\n", encoding="utf-8")
    cleared = list(names)
    if (folder / "usage").exists():
        shutil.rmtree(folder / "usage")
        cleared.append("usage/")
    return cleared
