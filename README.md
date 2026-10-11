# git-daily-sync

A small, cross-platform Python tool that commits and pushes one git repository, at most once a day, with safety checks in front of every commit.

It is meant for repositories where a daily snapshot is useful (notes, documentation, data folders, a backup-style repo) and where a commit should never go out unreviewed if it contains a secret or an oversized file.

## What it does

Each run performs these steps, in order:

1. **Once-a-day gate.** If today's log file already records a success line (`OK:` or `NO CHANGES`), the run exits 0 without touching the repository.
2. **Branch check.** The checked-out branch must equal `--expect-branch` (default `main`). With `--branch`, that name must also be the checked-out branch. Detached HEAD is refused.
3. **Fetch and fast-forward.** `git fetch origin`, then `git merge --ff-only origin/<branch>`. If the branch has diverged, the run is refused.
4. **Pending-commit check.** If local commits exist that are not on `origin/<branch>`, the run is refused. The tool never pushes history it did not check in the same run.
5. **Stage.** `git add -A`. If nothing is staged, the run logs `NO CHANGES` and exits 0.
6. **Size guard.** Any staged file larger than 10 MiB causes the run to unstage everything, log the file name, and exit 1.
7. **Secret scan.** The added lines of the staged diff are checked against a set of token and key patterns (see below). On a match, the run unstages everything, logs the file names and line numbers (never the matched text), and exits 1.
8. **Commit.** `Daily sync YYYY-MM-DD: N files`.
9. **Push.** `git push origin <branch>` as a fast-forward push. The push is never forced. A failed push is logged and exits 1, and the local commit is kept.

Every outcome is appended to `<log-dir>/sync-YYYY-MM-DD.log`, one line per event, prefixed with a timestamp.

## Why it is safe

- **No secret values in output.** The scanner reports the file, the line number and the rule name. Logs and console output never include the matched text. The tests check this.
- **Nothing is committed when a check fails.** Every refusal before the push unstages the index. The working tree is never modified by a refusal.
- **Fast-forward only.** The tool never runs `--force` and never rewrites history. Diverged branches are refused for a human to resolve.
- **Once-a-day gate.** A success line for today stops further runs, so a scheduler that fires repeatedly cannot produce a stream of commits.
- **Local-only logs.** The default log directory is added to `.git/info/exclude`, which is local to the clone. The log is never committed.
- **Credentials are not prompted for.** `GIT_TERMINAL_PROMPT=0` makes a missing credential fail instead of hanging a scheduled job. URLs with embedded credentials are redacted from logged git errors.

### Secret patterns scanned

| Rule | Shape |
|---|---|
| AWS access key id | `AKIA` or `ASIA` followed by 16 uppercase letters or digits |
| GitHub token | `ghp_` or `gho_` followed by 20+ characters |
| GitHub fine-grained token | `github_pat_` followed by 20+ characters |
| Google API key | `AIza` followed by 35 characters |
| OpenAI-style key | `sk-` followed by 20+ characters |
| PEM private key | `-----BEGIN ... PRIVATE KEY-----` header |
| Hard-coded credential | `api_key`, `secret`, `password` or `token` followed by `=` or `:` and a quoted value of 16+ characters |

## Requirements

- Python 3.9 or newer (standard library only; developed and tested on Python 3.12)
- `git` on `PATH`
- For tests: `pytest` (see `requirements-dev.txt`)

## Usage

Run from the folder that contains the `git_daily_sync` package, or add that folder to `PYTHONPATH`:

```bash
# Sync the repository at /path/to/repo (must be on main)
python -m git_daily_sync --repo /path/to/repo

# Report what would be committed, without committing or pushing
python -m git_daily_sync --repo /path/to/repo --dry-run

# Choose a different expected branch and log directory
python -m git_daily_sync --repo /path/to/repo --expect-branch trunk --log-dir /path/to/logs
```

Options:

| Option | Default | Meaning |
|---|---|---|
| `--repo PATH` | (required) | Root of the git repository |
| `--branch NAME` | checked-out branch | Branch to sync; must be the checked-out branch |
| `--expect-branch NAME` | `main` | Refuse unless the branch is this one |
| `--log-dir DIR` | `<repo>/.git-daily-sync` | Where daily logs are written |
| `--dry-run` | off | Run every step up to the commit, report, then unstage and stop |

Exit codes: `0` success or no-op, `1` refused or failed, `2` bad arguments.

Example log lines:

```
2026-10-11T09:00:02 OK: committed 3f2a9c1b0d4e (3 files) and pushed to origin/main
2026-10-12T09:00:01 NO CHANGES: nothing to commit
2026-10-13T09:00:01 FAIL: possible secret in staged changes, nothing committed: notes/setup.md line 14 (AWS access key id)
```

## Scheduling

Schedule the command once a day, at a time when the day's work is usually finished. Changes made after the run are picked up on the next day, because the day counts as done once it has a success line.

### Windows Task Scheduler

Create a small batch file, for example `<path-to-tool>\run-sync.bat`:

```bat
@echo off
cd /d "<path-to-tool>"
python -m git_daily_sync --repo "<path-to-repo>" >> "<path-to-logs>\scheduler.txt" 2>&1
```

Register it to run daily at 09:00:

```bat
schtasks /Create /TN "git-daily-sync" /TR "<path-to-tool>\run-sync.bat" /SC DAILY /ST 09:00
```

The scheduled account needs non-interactive push access to the remote (for example a credential helper or an SSH key). Otherwise the push fails and the run logs the failure.

### cron (Linux or macOS)

```cron
0 9 * * * cd "<path-to-tool>" && python3 -m git_daily_sync --repo "<path-to-repo>" >> "<path-to-logs>/scheduler.txt" 2>&1
```

Use the same non-interactive push access as above.

## Tests

The tests create a bare remote, a clone, and (where needed) a second clone, all under pytest's temporary directory. They never touch a real repository, and they ignore the global and system git configuration.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Coverage: no-change path, successful commit and push, secret refusal (including a check that the secret value appears nowhere in logs or console output), oversize file refusal, same-day re-run as a no-op, dry run, diverged branch refusal, feature-branch refusal, failed push (no success recorded, nothing forced), parsing of added diff lines, the secret patterns, usage errors, and the module entry point.

## Limitations

- **Not a full secret scanner.** It checks added lines against a fixed set of patterns. It cannot find secrets in other formats, and it reads binary files as text, so secrets inside binary data may be missed.
- **Deletions are committed.** `git add -A` stages deleted files too. Review what a run would delete with `--dry-run` if that matters for your repository.
- **Ignored files stay out.** Files matched by `.gitignore` are never staged. Files that are already tracked are not affected.
- **One remote, one branch.** It only talks to `origin` and only syncs the checked-out branch.
- **A quiet day is a finished day.** A run with no changes writes `NO CHANGES`, and that closes the day. Changes made later that day wait until the next run.
- **Failed pushes block later runs.** A failed push leaves one local commit. The next run refuses until that commit is pushed or reviewed by hand. This is deliberate, so unreviewed history is never pushed automatically.
- **No lock.** Two simultaneous runs against the same clone can race. Schedule one job per repository.
- **Size and day are local.** The 10 MiB limit applies to each staged file, not the whole commit. The "day" is the local calendar date of the machine.
- **Slower on Windows tests.** The suite starts many git processes, so a full run takes about a minute and a half on Windows.

## License

MIT. See [LICENSE](LICENSE).
