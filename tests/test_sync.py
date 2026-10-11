"""End-to-end tests against a temporary bare remote and clone. Nothing touches a real repository."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

from git_daily_sync.__main__ import main
from git_daily_sync.sync import MAX_FILE_BYTES

from .helpers import Sandbox, git, out, write

TODAY = date.today().isoformat()


def log_text(clone: Path) -> str:
    log = clone / ".git-daily-sync" / f"sync-{TODAY}.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def staged_is_empty(clone: Path) -> bool:
    return git(clone, "diff", "--cached", "--quiet", check=False).returncode == 0


def test_no_changes_logs_no_changes_and_exits_zero(sandbox: Sandbox) -> None:
    head_before = out(sandbox.clone, "rev-parse", "HEAD")

    assert main(["--repo", str(sandbox.clone)]) == 0

    assert "NO CHANGES" in log_text(sandbox.clone)
    assert out(sandbox.clone, "rev-parse", "HEAD") == head_before
    assert sandbox.remote_head() == head_before


def test_commit_and_push_success(sandbox: Sandbox, capsys) -> None:
    write(sandbox.clone, "notes/hello.txt", "hello\n")
    local_before = out(sandbox.clone, "rev-parse", "HEAD")

    assert main(["--repo", str(sandbox.clone)]) == 0

    new_head = out(sandbox.clone, "rev-parse", "HEAD")
    assert new_head != local_before
    assert sandbox.remote_head() == new_head
    assert out(sandbox.bare, "log", "-1", "--format=%s", "main") == f"Daily sync {TODAY}: 1 files"
    assert "OK:" in log_text(sandbox.clone)

    # The log directory is excluded locally and never reaches the remote.
    assert not any(p.startswith(".git-daily-sync") for p in sandbox.remote_files())
    exclude = (sandbox.clone / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.git-daily-sync/" in exclude.splitlines()


def test_secret_is_refused_nothing_committed_and_value_never_logged(sandbox: Sandbox, capsys) -> None:
    secret = "AKIA" + "7K3QZ9WXR2LMPT8N"
    write(sandbox.clone, "config.py", f'AWS_ACCESS_KEY = "{secret}"\n')
    head_before = out(sandbox.clone, "rev-parse", "HEAD")

    assert main(["--repo", str(sandbox.clone)]) == 1

    assert out(sandbox.clone, "rev-parse", "HEAD") == head_before
    assert sandbox.remote_head() == head_before
    assert staged_is_empty(sandbox.clone)
    assert (sandbox.clone / "config.py").exists()  # the file itself is left alone, just unstaged

    log = log_text(sandbox.clone)
    assert "config.py line 1" in log
    assert "FAIL" in log
    assert secret not in log

    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_oversize_file_is_refused(sandbox: Sandbox) -> None:
    big = sandbox.clone / "big.bin"
    with big.open("wb") as fh:
        fh.truncate(MAX_FILE_BYTES + 1)
    head_before = out(sandbox.clone, "rev-parse", "HEAD")

    assert main(["--repo", str(sandbox.clone)]) == 1

    assert sandbox.remote_head() == head_before
    assert staged_is_empty(sandbox.clone)
    assert "big.bin" in log_text(sandbox.clone)


def test_same_day_second_run_is_a_no_op(sandbox: Sandbox) -> None:
    write(sandbox.clone, "first.txt", "one\n")
    assert main(["--repo", str(sandbox.clone)]) == 0
    pushed = sandbox.remote_head()

    write(sandbox.clone, "second.txt", "two\n")
    assert main(["--repo", str(sandbox.clone)]) == 0

    assert sandbox.remote_head() == pushed
    assert "second.txt" not in sandbox.remote_files()
    assert (sandbox.clone / "second.txt").exists()
    assert "?? second.txt" in out(sandbox.clone, "status", "--porcelain")


def test_dry_run_commits_nothing_and_does_not_block_the_real_run(sandbox: Sandbox, capsys) -> None:
    write(sandbox.clone, "draft.txt", "draft\n")
    head_before = out(sandbox.clone, "rev-parse", "HEAD")

    assert main(["--repo", str(sandbox.clone), "--dry-run"]) == 0

    assert out(sandbox.clone, "rev-parse", "HEAD") == head_before
    assert sandbox.remote_head() == head_before
    assert staged_is_empty(sandbox.clone)
    assert "DRY-RUN: would commit 1 files" in capsys.readouterr().out
    assert "OK:" not in log_text(sandbox.clone)

    # A dry run must not satisfy the once-a-day gate, so the real run still happens.
    assert main(["--repo", str(sandbox.clone)]) == 0
    assert sandbox.remote_head() != head_before


def test_diverged_branch_is_refused(sandbox: Sandbox) -> None:
    other = sandbox.other_clone()
    write(other, "remote_side.txt", "remote\n")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "remote-side change")
    git(other, "push", "-q", "origin", "main")
    remote_before = sandbox.remote_head()

    git(sandbox.clone, "commit", "-q", "--allow-empty", "-m", "local-only commit")
    local_before = out(sandbox.clone, "rev-parse", "HEAD")
    write(sandbox.clone, "mine.txt", "mine\n")

    assert main(["--repo", str(sandbox.clone)]) == 1

    assert "diverged" in log_text(sandbox.clone)
    assert out(sandbox.clone, "rev-parse", "HEAD") == local_before
    assert sandbox.remote_head() == remote_before
    assert "mine.txt" not in sandbox.remote_files()


def test_feature_branch_is_refused(sandbox: Sandbox) -> None:
    git(sandbox.clone, "checkout", "-q", "-b", "feature")
    write(sandbox.clone, "work.txt", "wip\n")
    remote_before = sandbox.remote_head()

    assert main(["--repo", str(sandbox.clone)]) == 1

    assert "expected branch 'main'" in log_text(sandbox.clone)
    assert sandbox.remote_head() == remote_before


def test_branch_argument_must_match_checked_out_branch(sandbox: Sandbox) -> None:
    write(sandbox.clone, "x.txt", "x\n")
    remote_before = sandbox.remote_head()

    assert main(["--repo", str(sandbox.clone), "--branch", "feature"]) == 1

    assert "is not the checked-out branch" in log_text(sandbox.clone)
    assert sandbox.remote_head() == remote_before


def test_failed_push_is_not_recorded_as_success_and_nothing_is_forced(sandbox: Sandbox) -> None:
    hook = sandbox.bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho rejected by test hook >&2\nexit 1\n", encoding="utf-8")
    write(sandbox.clone, "unpushed.txt", "data\n")
    remote_before = sandbox.remote_head()

    assert main(["--repo", str(sandbox.clone)]) == 1

    log = log_text(sandbox.clone)
    assert "push failed" in log
    assert "OK:" not in log
    assert sandbox.remote_head() == remote_before
    assert out(sandbox.clone, "rev-list", "--count", "origin/main..HEAD") == "1"

    # The next run refuses until the local commit is pushed or reviewed by hand.
    hook.unlink()
    assert main(["--repo", str(sandbox.clone)]) == 1
    assert "local commit(s) not on origin/main" in log_text(sandbox.clone)
    assert sandbox.remote_head() == remote_before


def test_missing_repo_argument_is_a_usage_error() -> None:
    assert main([]) == 2


def test_non_repository_path_is_a_usage_error(tmp_path: Path) -> None:
    plain = tmp_path / "plain-folder"
    plain.mkdir()
    assert main(["--repo", str(plain)]) == 2


def test_module_entry_point_prints_help() -> None:
    project_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-m", "git_daily_sync", "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "--dry-run" in proc.stdout
