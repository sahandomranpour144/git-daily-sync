"""Explicit wrapper around the git command line.

Every git call goes through ``run_git``. A failure raises ``GitError`` with a
short, redacted message, so callers can log it without leaking credentials or
raw tracebacks.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

DEFAULT_TIMEOUT_S = 600
REMOTE = "origin"

_URL_CREDENTIALS = re.compile(r"(://)[^/\s@]+@")
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class GitError(RuntimeError):
    """A git command failed. ``str(error)`` is safe to write to a log."""


@dataclass(frozen=True)
class AddedLine:
    """One line added by the staged diff: file path, line number in the new file, text."""

    path: str
    line: int
    text: str


def redact(text: str) -> str:
    """Make git output one line long and strip credentials embedded in URLs."""
    text = _URL_CREDENTIALS.sub(r"\1***@", text or "")
    joined = " | ".join(line.strip() for line in text.splitlines() if line.strip())
    return joined[:400]


def run_git(
    repo: Path,
    *args: str,
    check: bool = True,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run ``git -C <repo> <args>`` and return the completed process.

    With ``check=True`` a non-zero exit raises ``GitError``. Terminal prompts
    are disabled so a missing credential fails fast instead of hanging.
    """
    cmd = ["git", "-C", str(repo), "-c", "core.quotePath=false", *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout}s") from exc

    if check and proc.returncode != 0:
        detail = redact(proc.stderr or proc.stdout)
        raise GitError(f"git {' '.join(args)} failed (exit {proc.returncode}): {detail}")
    return proc


# --- repository state -------------------------------------------------------

def is_repo_root(repo: Path) -> bool:
    """True only if ``repo`` is the top level of a git work tree (not a subfolder)."""
    proc = run_git(repo, "rev-parse", "--show-toplevel", check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return False
    return Path(proc.stdout.strip()).resolve() == repo.resolve()


def current_branch(repo: Path) -> str | None:
    """Name of the checked-out branch, or None when HEAD is detached."""
    proc = run_git(repo, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    name = proc.stdout.strip()
    return name if proc.returncode == 0 and name else None


def fetch(repo: Path) -> None:
    run_git(repo, "fetch", REMOTE)


def remote_branch_exists(repo: Path, branch: str) -> bool:
    proc = run_git(
        repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{REMOTE}/{branch}", check=False
    )
    return proc.returncode == 0


def ff_merge(repo: Path, upstream: str) -> None:
    """Fast-forward only. Raises GitError if the branch has diverged."""
    run_git(repo, "merge", "--ff-only", "--quiet", upstream)


def commits_ahead(repo: Path, upstream: str) -> int:
    out = run_git(repo, "rev-list", "--count", f"{upstream}..HEAD").stdout.strip()
    return int(out or 0)


def head_sha(repo: Path) -> str:
    return run_git(repo, "rev-parse", "HEAD").stdout.strip()


# --- staging, scanning, committing ------------------------------------------

def add_all(repo: Path) -> None:
    run_git(repo, "add", "-A")


def reset_index(repo: Path) -> None:
    """Unstage everything. The working tree is left untouched."""
    run_git(repo, "reset", "-q")


def staged_paths(repo: Path) -> list[str]:
    out = run_git(repo, "diff", "--cached", "--name-only", "--no-renames", "-z").stdout
    return [p for p in out.split("\0") if p]


def worktree_size(repo: Path, rel_path: str) -> int:
    """Size in bytes of a regular file in the work tree (0 for deleted or non-files)."""
    try:
        st = os.lstat(repo / rel_path)
    except FileNotFoundError:
        return 0
    return st.st_size if stat.S_ISREG(st.st_mode) else 0


def staged_added_lines(repo: Path) -> Iterator[AddedLine]:
    """Lines added by the staged diff. ``--text`` makes binary files scannable too."""
    diff = run_git(
        repo, "diff", "--cached", "--no-renames", "--no-color", "--no-ext-diff", "--text", "-U0"
    ).stdout
    return parse_added_lines(diff)


def parse_added_lines(diff_text: str) -> Iterator[AddedLine]:
    """Parse a unified diff (``-U0``) and yield only the added lines.

    Header lines (``+++ b/...``) are recognised only outside a hunk, so an
    added line whose content starts with ``++ `` is still reported correctly.
    """
    path: str | None = None
    in_hunk = False
    new_line = 0
    for raw in diff_text.split("\n"):
        if raw.startswith("diff --git "):
            in_hunk = False
            path = None
            continue
        match = _HUNK_HEADER.match(raw)
        if match:
            in_hunk = True
            new_line = int(match.group(1))
            continue
        if not in_hunk:
            if raw.startswith("+++ "):
                path = _diff_path(raw[4:])
            continue
        if raw.startswith("+"):
            if path is not None:
                yield AddedLine(path, new_line, raw[1:])
            new_line += 1
        # "-" lines and "\ No newline" markers do not advance the new-file counter.


def _diff_path(text: str) -> str | None:
    text = text.rstrip("\r")
    if text == "/dev/null":
        return None
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    return text[2:] if text.startswith("b/") else text


def commit(repo: Path, message: str) -> None:
    run_git(repo, "commit", "-q", "-m", message)


def push(repo: Path, branch: str) -> None:
    """Fast-forward push of one branch. Never forced."""
    run_git(repo, "push", REMOTE, f"refs/heads/{branch}:refs/heads/{branch}")


# --- local configuration -----------------------------------------------------

def add_info_exclude(repo: Path, pattern: str) -> None:
    """Add ``pattern`` to .git/info/exclude (local only, never committed)."""
    raw = run_git(repo, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if pattern in existing.splitlines():
        return
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write("# git-daily-sync log directory\n")
        fh.write(pattern + "\n")
