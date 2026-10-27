"""Token audit log: every AI call's model, tokens, time and estimated cost.

Each call is appended as one JSON line to state/<profile>/usage/<YYYY-MM>.jsonl, which the
workflow commits with the rest of state/. `forge usage` totals it by month and by agent, and
`write_report` turns one run into an audit report under reports/ that the workflow uploads.
Costs are estimates from the per-model prices in config/profiles.yaml (ai.pricing).
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_DIR = Path("state")
DEFAULT_REPORT_DIR = Path("reports")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
# What each agent's tokens pay for, in workflow order.
PHASES = {
    "analyst": "Photo scanning",
    "planner": "Choosing the photo",
    "caption_writer": "Caption, quote and alt text",
    "hashtags": "Hashtags",
    "reviewer": "Review",
}


def estimate_cost(model: str, tokens: dict, pricing: dict) -> float | None:
    """USD estimate. A model's cache_write and cache_read prices default to 1.25x and 0.1x its input price."""
    price = pricing.get(model)
    if not price:
        return None
    per_in, per_out = float(price["input"]) / 1e6, float(price["output"]) / 1e6
    per_write = float(price.get("cache_write", float(price["input"]) * 1.25)) / 1e6
    per_read = float(price.get("cache_read", float(price["input"]) * 0.1)) / 1e6
    cost = (
        tokens.get("input_tokens", 0) * per_in
        + tokens.get("cache_creation_input_tokens", 0) * per_write
        + tokens.get("cache_read_input_tokens", 0) * per_read
        + tokens.get("output_tokens", 0) * per_out
    )
    return round(cost, 6)


class UsageLog:
    def __init__(self, profile: str, *, pricing: dict | None = None, command: str = "", state_dir: Path = DEFAULT_STATE_DIR):
        self.profile = profile
        self.pricing = pricing or {}
        self.command = command
        self.state_dir = state_dir
        self.dir = state_dir / profile / "usage"
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.entries: list[dict] = []

    def record(
        self, *, agent: str, model: str, provider: str = "claude", tokens: dict | None = None,
        seconds: float = 0.0, ok: bool = True, error: str = "",
    ) -> dict:
        tokens = {f: int((tokens or {}).get(f, 0) or 0) for f in TOKEN_FIELDS}
        now = datetime.now(timezone.utc)
        entry = {
            "at": now.isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "command": self.command,
            "agent": agent,
            "provider": provider,
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


def _phase_rows(entries: list[dict]) -> list[dict]:
    rows: dict[str, dict] = {}
    for e in entries:
        row = rows.setdefault(e["agent"], {"agent": e["agent"], "phase": PHASES.get(e["agent"], e["agent"]), "calls": 0, "failed": 0,
                                           **{f: 0 for f in TOKEN_FIELDS}, "estimated_usd": 0.0, "seconds": 0.0})
        row["calls"] += 1
        row["failed"] += 0 if e.get("ok", True) else 1
        for f in TOKEN_FIELDS:
            row[f] += e.get(f, 0)
        row["estimated_usd"] = round(row["estimated_usd"] + (e.get("estimated_usd") or 0), 6)
        row["seconds"] = round(row["seconds"] + e.get("seconds", 0), 2)
    order = list(PHASES)
    return sorted(rows.values(), key=lambda r: (order.index(r["agent"]) if r["agent"] in order else len(order), r["agent"]))


def _phase_table(rows: list[dict]) -> list[str]:
    lines = [
        "| Phase | Agent | Calls | Input | Output | Cache writes | Cache reads | Total tokens | Est. USD | Seconds |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    total = {f: 0 for f in (*TOKEN_FIELDS, "calls", "estimated_usd", "seconds")}
    for r in rows:
        tokens = sum(r[f] for f in TOKEN_FIELDS)
        failed = f" ({r['failed']} failed)" if r["failed"] else ""
        lines.append(
            f"| {r['phase']} | {r['agent']} | {r['calls']}{failed} | {r['input_tokens']:,} | {r['output_tokens']:,} | "
            f"{r['cache_creation_input_tokens']:,} | {r['cache_read_input_tokens']:,} | {tokens:,} | ${r['estimated_usd']:.4f} | {r['seconds']:.1f} |"
        )
        for f in total:
            total[f] += r[f]
    lines.append(
        f"| **Total** | | {total['calls']} | {total['input_tokens']:,} | {total['output_tokens']:,} | {total['cache_creation_input_tokens']:,} | "
        f"{total['cache_read_input_tokens']:,} | {sum(total[f] for f in TOKEN_FIELDS):,} | ${total['estimated_usd']:.4f} | {total['seconds']:.1f} |"
    )
    return lines


def write_report(log: UsageLog, out_dir: Path = DEFAULT_REPORT_DIR) -> Path | None:
    """Write this run's token audit as Markdown and JSON; returns the Markdown path, or None without AI calls."""
    if not log.entries:
        return None
    rows = _phase_rows(log.entries)
    month = log.entries[0]["at"][:7]
    month_entries = load_month(log.profile, month, log.state_dir)
    month_rows = _phase_rows(month_entries)
    models = sorted({e["model"] for e in log.entries})
    providers = sorted({e.get("provider", "claude") for e in log.entries})
    lines = [
        f"# Token audit: {log.profile}, {log.command or 'run'}",
        "",
        f"- **Run:** `{log.run_id}`",
        f"- **Started:** {log.entries[0]['at']}",
        f"- **AI provider:** {', '.join(providers)}",
        f"- **Models:** {', '.join(models)}",
        "",
        "## This run, by phase",
        "",
        *_phase_table(rows),
        "",
        "Output tokens include the model's thinking. The caption writer runs again for a shortening pass or a revision, "
        "and its quote is written in the same call as the caption.",
        "",
        "## Every call",
        "",
        "| # | Time (UTC) | Agent | Model | Input | Output | Cache reads | Est. USD | Seconds | Result |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for n, e in enumerate(log.entries, 1):
        result = "ok" if e.get("ok", True) else f"failed: {e.get('error', '')[:80]}"
        cost = "n/a" if e.get("estimated_usd") is None else f"${e['estimated_usd']:.4f}"
        lines.append(
            f"| {n} | {e['at'][11:19]} | {e['agent']} | {e['model']} | {e.get('input_tokens', 0) + e.get('cache_creation_input_tokens', 0):,} | "
            f"{e.get('output_tokens', 0):,} | {e.get('cache_read_input_tokens', 0):,} | {cost} | {e.get('seconds', 0):.1f} | {result} |"
        )
    runs = len({e["run_id"] for e in month_entries})
    lines += ["", f"## {month} so far ({runs} runs)", "", *_phase_table(month_rows), "",
              "Costs are estimates from ai.pricing in config/profiles.yaml."]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{log.profile}-{log.run_id}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data = {"profile": log.profile, "command": log.command, "run_id": log.run_id, "providers": providers, "models": models,
            "phases": rows, "calls": log.entries, "month": {"month": month, "runs": runs, "phases": month_rows}}
    path.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
