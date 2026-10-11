"""Shared fixtures. Every repository used by the tests lives under pytest's tmp_path."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from .helpers import Sandbox, git, set_identity


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path_factory, monkeypatch):
    """Ignore the developer's global and system git config during tests."""
    cfg = tmp_path_factory.mktemp("gitconfig") / "empty.gitconfig"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    """A bare remote on branch main, and a clone of it that is ready to sync."""
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"

    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(bare, "symbolic-ref", "HEAD", "refs/heads/main")

    subprocess.run(["git", "init", "-q", str(seed)], check=True)
    git(seed, "symbolic-ref", "HEAD", "refs/heads/main")
    set_identity(seed)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "-q", "origin", "main")

    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    set_identity(clone)
    return Sandbox(root=tmp_path, bare=bare, clone=clone)
