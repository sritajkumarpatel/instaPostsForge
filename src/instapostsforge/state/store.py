"""Everything the workflow remembers for one profile, kept in state/<profile>/.

posts.json     every photo posted, by Drive file ID, so none is ever reused
catalog.json   the photo analyst's notes per photo, so each photo is analysed once
insights.json  reach, likes, saves and shares per post, used by the planner and hashtag strategist

None of these files hold secrets; the workflow commits them back to the repo after each post.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_DIR = Path("state")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProfileState:
    def __init__(self, profile: str, state_dir: Path = DEFAULT_STATE_DIR):
        self.dir = state_dir / profile
        self.posts: dict = self._load("posts.json")
        self.catalog: dict = self._load("catalog.json")
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
        return set(self.posts)

    def is_posted(self, photo_id: str) -> bool:
        return photo_id in self.posts

    def history(self, limit: int = 30) -> list[dict]:
        """Recent posts, newest first, joined with their analysis and results."""
        entries = sorted(self.posts.items(), key=lambda kv: kv[1].get("posted_at", ""), reverse=True)[:limit]
        out = []
        for photo_id, post in entries:
            analysis = self.catalog.get(photo_id, {}).get("analysis", {})
            out.append(
                {
                    "posted_at": post.get("posted_at"),
                    "subject": analysis.get("subject"),
                    "mood": analysis.get("mood"),
                    "style": analysis.get("style"),
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

    def record_post(self, photo_id: str, *, photo_name: str, media_id: str, caption: str, hashtags: list[str], plan_reason: str) -> None:
        self.posts[photo_id] = {
            "name": photo_name,
            "media_id": media_id,
            "caption": caption,
            "hashtags": hashtags,
            "plan_reason": plan_reason,
            "posted_at": _now(),
        }
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
        return self.catalog.get(photo_id, {}).get("analysis")

    def save_analysis(self, photo_id: str, photo_name: str, analysis: dict) -> None:
        self.catalog[photo_id] = {"name": photo_name, "analysis": analysis, "analysed_at": _now()}
        self._save("catalog.json", self.catalog)

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
