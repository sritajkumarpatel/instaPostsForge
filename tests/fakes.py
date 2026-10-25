"""Fake sources, AI, host and publisher so the workflow can be tested without accounts."""

from __future__ import annotations

from instapostsforge.agents import AIWorkflow, PlanItem, Review
from instapostsforge.hosting import ImageHost
from instapostsforge.models import Caption, Photo
from instapostsforge.publishers import Publisher
from instapostsforge.sources import PhotoSource


class FakeSource(PhotoSource):
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.downloads = 0

    def list_photos(self):
        return [Photo(id=name, name=name) for name in sorted(self.files)]

    def download(self, photo):
        self.downloads += 1
        return self.files[photo.id]


class FakeAI(AIWorkflow):
    """Scripted answers; records what each agent was asked."""

    def __init__(self, *, plan_order=None, captions=None, tag_sets=None, reviews=None, fail=(), analyses=None, quotes=None):
        self.plan_order = plan_order
        self.captions = list(captions or [])
        self.tag_sets = list(tag_sets or [])
        self.reviews = list(reviews or [])
        self.fail = set(fail)
        self.analyses = analyses or {}
        self.quotes = list(quotes or [])
        self.calls: dict[str, list] = {"analyze": [], "plan": [], "caption": [], "tags": [], "review": []}

    def analyze(self, image_jpeg, *, photo_name):
        self.calls["analyze"].append(photo_name)
        if "analyze" in self.fail:
            raise RuntimeError("analyst down")
        base = {"subject": f"subject of {photo_name}", "category": "street", "mood": "calm", "quality_score": 7}
        return {**base, **self.analyses.get(photo_name, {})}

    def plan(self, candidates, history, *, today, horizon):
        self.calls["plan"].append({"candidates": candidates, "history": history, "horizon": horizon})
        if "plan" in self.fail:
            raise RuntimeError("planner down")
        order = self.plan_order or [c["photo_id"] for c in candidates]
        return [PlanItem(pid, f"because {pid}", f"angle for {pid}") for pid in order]

    def write_caption(self, image_jpeg, *, analysis, angle, recent_captions, top_captions, feedback=""):
        self.calls["caption"].append({"angle": angle, "feedback": feedback, "analysis": analysis})
        text = self.captions.pop(0) if self.captions else "Quiet streets after rain."
        quote = self.quotes.pop(0) if self.quotes else None
        return Caption(text=text, hashtags=[], alt_text="A wet street at night.", quote=quote)

    def choose_hashtags(self, *, analysis, caption_text, recent_tag_sets, tag_stats, feedback=""):
        self.calls["tags"].append({"recent": recent_tag_sets, "stats": tag_stats, "feedback": feedback})
        return self.tag_sets.pop(0) if self.tag_sets else ["#streetphotography", "#rain", "#citylights"]

    def review(self, image_jpeg, *, caption, analysis=None):
        self.calls["review"].append(caption.render())
        return self.reviews.pop(0) if self.reviews else Review(approved=True)


class FakeHost(ImageHost):
    def __init__(self):
        self.uploaded, self.deleted = {}, []

    def upload(self, key, jpeg):
        self.uploaded[key] = jpeg
        return f"https://example.com/{key}"

    def delete(self, key):
        self.deleted.append(key)


class FakePublisher(Publisher):
    def __init__(self, insights=None):
        self.posts = []
        self.insights = insights or {}
        self.counter = 0

    def publish_image(self, image_url, caption, alt_text=None):
        self.counter += 1
        self.posts.append((image_url, caption, alt_text))
        return f"media-{self.counter}"

    def fetch_insights(self, media_id, metrics):
        return self.insights.get(media_id, {"reach": 100})
