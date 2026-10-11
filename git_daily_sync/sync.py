"""The daily sync steps.

Each step either passes or stops the run with a logged reason. Nothing is ever
force-pushed, and the index is unstaged whenever the run stops before a commit.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from . import gitops
from .gitops import GitError
from .secrets import scan_lines

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB
DEFAULT_EXPECT_BRANCH = "main"
DEFAULT_LOG_DIRNAME = ".git-daily-sync"
SUCCESS_PREFIXES = ("OK:", "NO CHANGES")
_BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class UsageError(ValueError):
    """Bad arguments (not a git repository, invalid branch name, ...). CLI exit 2."""


class Refused(Exception):
    """A safety check or git step stopped the run. CLI exit 1."""


class SyncLog:
    """Append-only daily log. Every line is also printed."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, message: str) -> None:
        message = message.replace("\r", " ").replace("\n", " | ")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {message}\n")
        print(message)


def already_succeeded(log_path: Path) -> bool:
    """True if today's log already records a success line (OK: or NO CHANGES)."""
    if not log_path.is_file():
        return False
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        _, _, message = line.partition(" ")
        if message.startswith(SUCCESS_PREFIXES):
            return True
    return False


def sync(
    repo: str | Path,
    *,
    branch: str | None = None,
    expect_branch: str = DEFAULT_EXPECT_BRANCH,
    log_dir: str | Path | None = None,
    dry_run: bool = False,
    today: date | None = None,
) -> int:
    """Run the daily sync. Returns the process exit code (0 or 1); raises UsageError for bad input."""
    repo_path = Path(repo).expanduser().resolve()
    if not repo_path.is_dir():
        raise UsageError(f"repository path is not a directory: {repo_path}")
    if not gitops.is_repo_root(repo_path):
        raise UsageError(f"not the top level of a git repository: {repo_path}")

    for name, value in (("--branch", branch), ("--expect-branch", expect_branch)):
        if value is not None and not _BRANCH_NAME.match(value):
            raise UsageError(f"invalid branch name for {name}: {value!r}")

    today = today or date.today()
    log_path_dir = (
        Path(log_dir).expanduser().resolve() if log_dir else repo_path / DEFAULT_LOG_DIRNAME
    )
    _exclude_log_dir(repo_path, log_path_dir)
    log = SyncLog(log_path_dir / f"sync-{today.isoformat()}.log")

    if already_succeeded(log.path):
        print("Already synced successfully today; nothing to do.")
        return 0

    try:
        _run(repo_path, log, branch, expect_branch, dry_run, today)
        return 0
    except (Refused, GitError) as exc:
        log.write(f"FAIL: {exc}")
        return 1


def _exclude_log_dir(repo: Path, log_dir: Path) -> None:
    try:
        rel = log_dir.relative_to(repo)
    except ValueError:
        return  # outside the repository: nothing to exclude
    if not rel.parts:
        raise UsageError("the log directory cannot be the repository root")
    gitops.add_info_exclude(repo, "/" + "/".join(rel.parts) + "/")


def _run(
    repo: Path,
    log: SyncLog,
    branch: str | None,
    expect_branch: str,
    dry_run: bool,
    today: date,
) -> None:
    current = gitops.current_branch(repo)
    if current is None:
        raise Refused("HEAD is detached; check out the branch to sync")
    target = branch or current
    if target != current:
        raise Refused(f"--branch {target!r} is not the checked-out branch {current!r}")
    if target != expect_branch:
        raise Refused(f"branch {target!r} is not the expected branch {expect_branch!r}")

    gitops.fetch(repo)
    if not gitops.remote_branch_exists(repo, target):
        raise Refused(f"{gitops.REMOTE}/{target} does not exist")
    upstream = f"{gitops.REMOTE}/{target}"

    try:
        gitops.ff_merge(repo, upstream)
    except GitError as exc:
        raise Refused(f"cannot fast-forward to {upstream}; branch has diverged ({exc})") from exc

    ahead = gitops.commits_ahead(repo, upstream)
    if ahead:
        raise Refused(
            f"{ahead} local commit(s) not on {upstream}; push or review them by hand "
            "(the tool never pushes history it did not scan in this run)"
        )

    gitops.add_all(repo)
    try:
        paths = _check_staged(repo)
    except (Refused, GitError):
        _unstage_quietly(repo)
        raise

    if not paths:
        if dry_run:
            log.write("DRY-RUN: nothing to commit")
        else:
            log.write("NO CHANGES: nothing to commit")
        return

    if dry_run:
        _unstage_quietly(repo)
        shown = ", ".join(paths[:50]) + (" ..." if len(paths) > 50 else "")
        log.write(f"DRY-RUN: would commit {len(paths)} files to {target}: {shown}")
        return

    message = f"Daily sync {today.isoformat()}: {len(paths)} files"
    try:
        gitops.commit(repo, message)
    except GitError:
        _unstage_quietly(repo)
        raise
    sha = gitops.head_sha(repo)[:12]

    try:
        gitops.push(repo, target)
    except GitError as exc:
        raise Refused(f"push failed; commit {sha} kept locally, nothing forced ({exc})") from exc

    log.write(f"OK: committed {sha} ({len(paths)} files) and pushed to {upstream}")


def _check_staged(repo: Path) -> list[str]:
    """Size and secret checks on the staged set. Raises Refused on the first problem."""
    paths = gitops.staged_paths(repo)
    for rel in paths:
        size = gitops.worktree_size(repo, rel)
        if size > MAX_FILE_BYTES:
            raise Refused(
                f"staged file over 10 MiB refused: {rel} ({size / (1024 * 1024):.1f} MiB)"
            )

    findings = scan_lines(gitops.staged_added_lines(repo))
    if findings:
        details = "; ".join(f"{f.path} line {f.line} ({f.rule})" for f in findings[:20])
        more = f" (+{len(findings) - 20} more)" if len(findings) > 20 else ""
        raise Refused(f"possible secret in staged changes, nothing committed: {details}{more}")
    return paths


def _unstage_quietly(repo: Path) -> None:
    try:
        gitops.reset_index(repo)
    except GitError:
        pass
