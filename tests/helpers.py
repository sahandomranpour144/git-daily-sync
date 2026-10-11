"""Helpers shared by the test modules. Every repository they create lives under tmp_path."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

IDENTITY = {"user.name": "Test Bot", "user.email": "test-bot@example.invalid"}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )


def out(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


def set_identity(repo: Path) -> None:
    for key, value in IDENTITY.items():
        git(repo, "config", key, value)


def write(repo: Path, rel: str, text: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@dataclass
class Sandbox:
    root: Path
    bare: Path
    clone: Path

    def other_clone(self, name: str = "other") -> Path:
        """A second clone of the same bare remote, used to create remote-side commits."""
        path = self.root / name
        subprocess.run(["git", "clone", "-q", str(self.bare), str(path)], check=True)
        set_identity(path)
        return path

    def remote_head(self, branch: str = "main") -> str:
        return out(self.bare, "rev-parse", f"refs/heads/{branch}")

    def remote_files(self, branch: str = "main") -> list[str]:
        return out(self.bare, "ls-tree", "-r", "--name-only", branch).splitlines()
