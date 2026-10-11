"""Unit tests that need no git repository: diff parsing and the secret regexes."""

from __future__ import annotations

import pytest

from git_daily_sync.gitops import AddedLine, parse_added_lines
from git_daily_sync.secrets import scan_lines, scan_text

# Fake values assembled at runtime so that no literal token shape sits in the source.
AWS_KEY = "AKIA" + "7K3QZ9WXR2LMPT8N"
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
GITHUB_PAT = "github_pat_" + "11ABCDEFG0123456789_abcdefghij"
GOOGLE_KEY = "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6q"
OPENAI_KEY = "sk-" + "abcdefghijklmnopqrstuvwx"
PEM_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    "line",
    [
        f'aws = "{AWS_KEY}"',
        f"token = '{GITHUB_TOKEN}'",
        f"use {GITHUB_PAT} here",
        f"key = {GOOGLE_KEY}",
        f"OPENAI_KEY={OPENAI_KEY}",
        PEM_HEADER,
        'password = "hunter2hunter2hunter2"',
        "API_KEY: 'abcdefghijklmnop1234'",
        'GITHUB_SECRET = "abcdefghijklmnopqrstu"',
    ],
)
def test_known_shapes_are_detected(line: str) -> None:
    assert scan_text(line) is not None


@pytest.mark.parametrize(
    "line",
    [
        "token_count = 5",
        'password = "short"',
        "max_tokens = 4096",
        "print('hello world')",
        "AKIA123",  # too short to be an access key id
        'name = "not-a-secret-value-at-all"',  # no keyword before the value
    ],
)
def test_ordinary_lines_are_not_flagged(line: str) -> None:
    assert scan_text(line) is None


def test_scan_lines_reports_path_and_line_but_never_the_value() -> None:
    lines = [
        AddedLine("app/config.py", 3, "x = 1"),
        AddedLine("app/config.py", 7, f'aws = "{AWS_KEY}"'),
    ]
    findings = scan_lines(lines)
    assert [(f.path, f.line) for f in findings] == [("app/config.py", 7)]
    assert AWS_KEY not in repr(findings)
    assert AWS_KEY not in findings[0].rule


DIFF = """\
diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1,0 +2,2 @@
+first
+++ second
@@ -10 +12 @@
-gone
+replaced
diff --git a/y.txt b/y.txt
--- /dev/null
+++ b/y.txt
@@ -0,0 +1 @@
+hello
"""


def test_parse_added_lines_tracks_paths_and_new_line_numbers() -> None:
    parsed = [(a.path, a.line, a.text) for a in parse_added_lines(DIFF)]
    assert parsed == [
        ("x.py", 2, "first"),
        ("x.py", 3, "++ second"),  # content that looks like a header is still an added line
        ("x.py", 12, "replaced"),
        ("y.txt", 1, "hello"),
    ]
