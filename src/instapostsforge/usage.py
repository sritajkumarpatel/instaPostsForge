"""Token audit log: every AI call's model, tokens, time and estimated cost.

Each call is appended as one JSON line to state/<profile>/usage/<YYYY-MM>.jsonl, which the
workflow commits with the rest of state/. `forge usage` totals it by month and by agent.
Costs are estimates from the per-model prices in config/profiles.yaml (ai.pricing).
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_DIR = Path("state")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def estimate_cost(model: str, tokens: dict, pricing: dict) -> float | None:
    """USD estimate. Cache writes bill at 1.25x input and cache reads at 0.1x input."""
    price = pricing.get(model)
    if not price:
        return None
    per_in, per_out = float(price["input"]) / 1e6, float(price["output"]) / 1e6
    cost = (
        tokens.get("input_tokens", 0) * per_in
        + tokens.get("cache_creation_input_tokens", 0) * per_in * 1.25
        + tokens.get("cache_read_input_tokens", 0) * per_in * 0.1
        + tokens.get("output_tokens", 0) * per_out
    )
    return round(cost, 6)


class UsageLog:
    def __init__(self, profile: str, *, pricing: dict | None = None, command: str = "", state_dir: Path = DEFAULT_STATE_DIR):
        self.profile = profile
        self.pricing = pricing or {}
        self.command = command
        self.dir = state_dir / profile / "usage"
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.entries: list[dict] = []

    def record(self, *, agent: str, model: str, usage=None, seconds: float = 0.0, ok: bool = True, error: str = "") -> dict:
        tokens = {f: int(getattr(usage, f, 0) or 0) for f in TOKEN_FIELDS} if usage is not None else {f: 0 for f in TOKEN_FIELDS}
        now = datetime.now(timezone.utc)
        entry = {
            "at": now.isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "command": self.command,
            "agent": agent,
            "model": model,
            **tokens,
            "estimated_usd": estimate_cost(model, tokens, self.pricing),
            "seconds": round(seconds, 2),
            "ok": ok,
        }
        if error:
            entry["error"] = error[:300]
        self.entries.append(entry)
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / f"{now:%Y-%m}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def summary(self) -> dict[str, dict]:
        return summarize(self.entries)


def summarize(entries: list[dict]) -> dict[str, dict]:
    """Totals per agent plus a "total" row."""
    rows: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "estimated_usd": 0.0})
    for e in entries:
        for key in (e["agent"], "total"):
            row = rows[key]
            row["calls"] += 1
            row["input_tokens"] += e.get("input_tokens", 0) + e.get("cache_creation_input_tokens", 0)
            row["output_tokens"] += e.get("output_tokens", 0)
            row["cache_read_input_tokens"] += e.get("cache_read_input_tokens", 0)
            row["estimated_usd"] = round(row["estimated_usd"] + (e.get("estimated_usd") or 0), 4)
    return dict(rows)


def load_month(profile: str, month: str, state_dir: Path = DEFAULT_STATE_DIR) -> list[dict]:
    path = state_dir / profile / "usage" / f"{month}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def format_table(rows: dict[str, dict]) -> list[str]:
    lines = ["| Agent | Calls | Input tokens | Output tokens | Cache reads | Est. USD |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for agent in sorted(rows, key=lambda a: (a == "total", a)):
        r = rows[agent]
        name = f"**{agent}**" if agent == "total" else agent
        lines.append(
            f"| {name} | {r['calls']} | {r['input_tokens']:,} | {r['output_tokens']:,} | {r['cache_read_input_tokens']:,} | ${r['estimated_usd']:.4f} |"
        )
    return lines
