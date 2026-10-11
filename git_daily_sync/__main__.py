"""Command-line entry point: ``python -m git_daily_sync --repo PATH``."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .sync import DEFAULT_EXPECT_BRANCH, UsageError, sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git_daily_sync",
        description="Commit and push a git repository at most once per day, with safety checks.",
    )
    parser.add_argument("--repo", required=True, metavar="PATH", help="root of the git repository")
    parser.add_argument(
        "--branch",
        metavar="NAME",
        help="branch to sync (default: the checked-out branch)",
    )
    parser.add_argument(
        "--expect-branch",
        default=DEFAULT_EXPECT_BRANCH,
        metavar="NAME",
        help=f"refuse unless the branch is this one (default: {DEFAULT_EXPECT_BRANCH})",
    )
    parser.add_argument(
        "--log-dir",
        metavar="DIR",
        help="log directory (default: <repo>/.git-daily-sync, excluded via .git/info/exclude)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run every step up to the commit, report what would be committed, then stop",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Exit codes: 0 success or no-op, 1 refused or failed, 2 bad arguments."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    try:
        return sync(
            args.repo,
            branch=args.branch,
            expect_branch=args.expect_branch,
            log_dir=args.log_dir,
            dry_run=args.dry_run,
        )
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
