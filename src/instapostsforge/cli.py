"""Command line entry point: `forge <command> --profile <name>`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import pipeline
from .config import DEFAULT_CONFIG_PATH, ConfigError, env_value, list_profiles, load_profile
from .usage import format_table, load_month, summarize


def _summary_lines(result) -> list[str]:
    if result.published:
        status = f"Published (media ID {result.media_id})"
    else:
        status = f"Preview only, nothing was posted to Instagram. Draft saved to `{result.draft_dir}`; to publish, run with mode **post**."
    lines = [
        f"## {result.profile}: {result.photo.name}",
        "",
        f"**Status:** {status}",
        f"**Why this photo:** {result.plan_reason}",
        "",
        "```",
        result.caption.render(),
        "```",
        f"**Alt text:** {result.caption.alt_text}",
    ]
    lines += [f"- {note}" for note in result.notes]
    if result.usage:
        lines += ["", "**AI usage this run**", "", *format_table(result.usage)]
    return lines


def _report(result) -> None:
    lines = _summary_lines(result)
    print("\n".join(lines))
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:  # shows on the GitHub Actions run page
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def cmd_profiles(args) -> int:
    for name in list_profiles(args.config):
        print(name)
    return 0


def cmd_preview(args) -> int:
    profile = load_profile(args.profile, args.config)
    _report(pipeline.run(profile, pipeline.Components.from_profile(profile, publish=False, command="preview"), publish=False))
    return 0


def cmd_post(args) -> int:
    profile = load_profile(args.profile, args.config)
    _report(pipeline.run(profile, pipeline.Components.from_profile(profile, publish=True, command="post"), publish=True))
    return 0


def cmd_plan(args) -> int:
    profile = load_profile(args.profile, args.config)
    components = pipeline.Components.from_profile(profile, publish=False, command="plan")
    plan = pipeline.plan_days(profile, components, args.days)
    for day, (photo, item) in enumerate(plan, start=1):
        print(f"Day {day}: {photo.name}\n  Why: {item.reason}\n  Angle: {item.angle}\n")
    print("\n".join(format_table(components.usage.summary())))
    return 0


def cmd_usage(args) -> int:
    from datetime import date

    month = args.month or date.today().strftime("%Y-%m")
    entries = load_month(args.profile, month)
    if not entries:
        print(f"No AI usage recorded for profile '{args.profile}' in {month}.")
        return 0
    runs = len({e["run_id"] for e in entries})
    failed = sum(1 for e in entries if not e.get("ok", True))
    print(f"AI usage for '{args.profile}' in {month}: {runs} runs, {len(entries)} calls, {failed} failed\n")
    print("\n".join(format_table(summarize(entries))))
    models = sorted({e["model"] for e in entries})
    print(f"\nModels used: {', '.join(models)}. Costs are estimates from ai.pricing in config/profiles.yaml.")
    return 0


def cmd_check(args) -> int:
    from .publishers import InstagramPublisher
    from .sources import build_source
    from .state import ProfileState

    profile = load_profile(args.profile, args.config)
    publisher = InstagramPublisher(profile.instagram)
    account = publisher.account()
    print(f"Instagram account: @{account.get('username')} ({account.get('account_type')})")
    print(f"Publishing quota (last 24h): {publisher.publishing_quota()}")
    photos = build_source(profile.source).list_photos()
    state = ProfileState(profile.name)
    print(f"Photos in source: {len(photos)}, not yet posted: {len([p for p in photos if not state.is_posted(p.id)])}")
    print(metadata_report(photos))
    return 0


def metadata_report(photos) -> str:
    """How many photos carry a capture time and GPS, which decides how well photos can be grouped."""
    from .imaging import name_time

    total = len(photos)
    if not total:
        return "Photo metadata: no photos to check."
    timed = sum(1 for p in photos if p.taken_at or name_time(p.name))
    located = sum(1 for p in photos if p.location)
    return (
        f"Photo metadata: {timed} of {total} have a capture time, {located} of {total} have a GPS location "
        "(locations are only counted here, never saved)."
    )


def cmd_refresh_token(args) -> int:
    from .publishers.instagram import refresh_long_lived_token

    profile = load_profile(args.profile, args.config)
    var = profile.instagram.get("access_token_env")
    data = refresh_long_lived_token(env_value(var))
    days = int(data.get("expires_in", 0)) // 86400
    if args.token_file:  # used by the refresh-token workflow, so the token never appears in logs
        Path(args.token_file).write_text(data["access_token"], encoding="utf-8")
        print(f"New token for {var} (valid about {days} days) written to {args.token_file}.")
        return 0
    print(f"New token (valid about {days} days). Replace {var} in .env and in your GitHub secrets:")
    print(data["access_token"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge", description="Daily Instagram photo poster with an AI workflow.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, type=Path)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("profiles", help="List configured profiles").set_defaults(func=cmd_profiles)
    for name, func, help_text in (
        ("preview", cmd_preview, "Run the whole workflow but save the post to drafts/ instead of publishing"),
        ("post", cmd_post, "Run the whole workflow and publish today's post"),
        ("plan", cmd_plan, "Show which photos the planner would post over the next days"),
        ("check", cmd_check, "Verify the Instagram token, quota and photo source"),
        ("refresh-token", cmd_refresh_token, "Extend the Instagram access token by 60 days"),
        ("usage", cmd_usage, "Show AI token usage and estimated cost for a month"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--profile", "-p", default="main")
        if name == "plan":
            p.add_argument("--days", type=int, default=7)
        if name == "refresh-token":
            p.add_argument("--token-file", help="Write the new token to this file instead of printing it")
        if name == "usage":
            p.add_argument("--month", help="YYYY-MM, default this month")
        p.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except (ConfigError, pipeline.NothingToPost, pipeline.ReviewRejected) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
