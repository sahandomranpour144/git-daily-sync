"""Regex scan of added lines for likely secrets.

This is a guard rail, not a full secret scanner. It only sees lines that a
commit would add, and it matches known token shapes plus one generic
``name = "value"`` pattern. Findings report the file, the line number and the
rule name. The matched text is never returned or logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .gitops import AddedLine


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


# Each pattern is linear in practice. The generic rule has no leading wildcard
# so that long lines cannot trigger quadratic backtracking.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[po]_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("OpenAI-style secret key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("PEM private key header", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    (
        "hard-coded credential",
        re.compile(
            r"""(?:api[_\-]?key|secret|password|token)[A-Za-z0-9_\-]*\s*[:=]\s*(["'])[^"'\s]{16,}\1""",
            re.IGNORECASE,
        ),
    ),
)


def scan_text(text: str) -> str | None:
    """Return the rule name of the first pattern that matches ``text``, or None."""
    for rule, pattern in PATTERNS:
        if pattern.search(text):
            return rule
    return None


def scan_lines(lines: Iterable[AddedLine]) -> list[Finding]:
    """Scan added lines. One finding per line at most."""
    findings: list[Finding] = []
    for added in lines:
        rule = scan_text(added.text)
        if rule is not None:
            findings.append(Finding(added.path, added.line, rule))
    return findings
