"""Command line entry point: `forge <command> --profile <name>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import pipeline
from .config import DEFAULT_CONFIG_PATH, ConfigError, env_value, list_profiles, load_profile


def _print_result(result) -> None:
    print(f"\nProfile: {result.profile}")
    print(f"Photo:   {result.photo.name}")
    print("\n" + result.caption.render() + "\n")
    print(f"Alt text: {result.caption.alt_text}")
    for note in result.notes:
        print(f"Note: {note}")
    if result.published:
        print(f"\nPublished. Media ID: {result.media_id}")
    else:
        print(f"\nDraft saved to {result.draft_dir} (nothing was posted).")


def cmd_profiles(args) -> int:
    for name in list_profiles(args.config):
        print(name)
    return 0


def cmd_preview(args) -> int:
    profile = load_profile(args.profile, args.config)
    components = pipeline.Components.from_profile(profile, publish=False)
    _print_result(pipeline.run(profile, components, publish=False))
    return 0


def cmd_post(args) -> int:
    profile = load_profile(args.profile, args.config)
    components = pipeline.Components.from_profile(profile, publish=True)
    _print_result(pipeline.run(profile, components, publish=True))
    return 0


def cmd_check(args) -> int:
    from .publishers import InstagramPublisher

    profile = load_profile(args.profile, args.config)
    publisher = InstagramPublisher(profile.instagram)
    account = publisher.account()
    print(f"Instagram account: @{account.get('username')} ({account.get('account_type')})")
    print(f"Publishing quota (last 24h): {publisher.publishing_quota()}")
    photos = pipeline.build_source(profile.source).list_photos()
    posted = pipeline.PostLog(profile.name).posted_ids()
    print(f"Photos in source: {len(photos)}, not yet posted: {len([p for p in photos if p.id not in posted])}")
    return 0


def cmd_refresh_token(args) -> int:
    from .publishers.instagram import refresh_long_lived_token

    profile = load_profile(args.profile, args.config)
    var = profile.instagram.get("access_token_env")
    data = refresh_long_lived_token(env_value(var))
    days = int(data.get("expires_in", 0)) // 86400
    print(f"New token (valid about {days} days). Replace {var} in .env and in your GitHub secrets:")
    print(data["access_token"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge", description="Daily Instagram photo poster.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, type=Path)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("profiles", help="List configured profiles").set_defaults(func=cmd_profiles)
    for name, func, help_text in (
        ("preview", cmd_preview, "Pick a photo and write its caption into drafts/ without posting"),
        ("post", cmd_post, "Pick a photo, write its caption, and publish it"),
        ("check", cmd_check, "Verify the Instagram token, quota and photo source"),
        ("refresh-token", cmd_refresh_token, "Extend the Instagram access token by 60 days"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--profile", "-p", default="main")
        p.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except (ConfigError, pipeline.NothingToPost) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
