"""Cluster unmatched files into series groups for sequential agent processing.

The agent processes one series group at a time so that later-item decisions
can cite already-resolved siblings ("sibling consistency"). Across groups,
the agent has no shared context — groups are genuinely independent.

Clustering is by a canonicalized title: lowercased, punctuation stripped
(except hyphens which are meaningful in series names like `Dead-Pool`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from filename_parser import parse_filename


_CANONICAL_STRIP_RE = re.compile(r"[^\w\s-]")
_MULTI_SPACE_RE = re.compile(r"\s+")


def canonical_series_key(raw_title: str) -> str:
    """Normalize a raw series title into a clustering key.

    Preserves alphanumerics, whitespace, and hyphens. Collapses multiple
    spaces. Lowercased.
    """
    s = _CANONICAL_STRIP_RE.sub("", raw_title.lower())
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def group_by_series(paths: list[Path]) -> dict[str, list[Path]]:
    """Return {canonical_key: [paths...]} grouping input paths by inferred series."""
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in paths:
        parsed = parse_filename(p.name)
        key = canonical_series_key(parsed["raw_title"])
        groups[key].append(p)
    # Sort each group's files for deterministic processing order (matters
    # for sibling-consistency: #1 resolves first, then #2 sees #1's decision).
    return {k: sorted(v, key=lambda p: p.name) for k, v in groups.items()}
