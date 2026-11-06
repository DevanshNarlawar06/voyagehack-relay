"""Action-name patterns: `comp.*` matches `comp.salary.change`; `*` matches everything."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase


def matches(pattern: str, action: str) -> bool:
    """True if dotted `action` matches `pattern` (shell-style wildcards, case-sensitive)."""
    return fnmatchcase(action, pattern)


def matches_any(patterns: Iterable[str], action: str) -> bool:
    """True if any pattern matches `action`."""
    return any(matches(p, action) for p in patterns)
