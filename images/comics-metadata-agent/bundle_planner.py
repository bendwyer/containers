"""Plan bundle-level canonical metadata before applying to the library.

Why bundle-aware: per-item apply produced edge cases that had to be
hand-patched (TPB-as-its-own-volume splits, per-year annual splits, lost
subtitles when collapsing Series, missing Title metadata). A bundle-aware
planner sees the whole decision log at once and emits a coherent plan:
items that should be one logical series get grouped, share Series + Volume
tags, get sequentially numbered if their CV issue numbers collide, and
preserve subtitles in Title.

Three phases:
  1. hydrate — fetch CV volume + issue for each match decision
  2. group   — cluster items into logical series
  3. plan    — assign canonical Series/Volume/Number/Title/Year per item

The output is a list of `ItemPlan` records that the caller (apply.py) feeds
to comictagger + a python ComicInfo.xml override pass.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any


# Sibling start-year distance beyond which we treat same-base-name volumes
# as unrelated relaunches (e.g., Batman 2011 vs Batman 2016). Within this,
# they're considered the same logical series.
SIBLING_YEAR_TOLERANCE = 3


@dataclass
class ItemPlan:
    """Resolved canonical metadata for one item, ready for apply."""
    filename: str
    issue_id: int
    volume_id: int
    series: str
    volume: int
    number: int
    title: str
    year: str | int | None


def plan_bundle(
    decisions: list[dict[str, Any]],
    cv_client,
) -> list[ItemPlan]:
    """Return a per-item plan for every match decision.

    `decisions` is the parsed `<source_id>.jsonl`. Only `decision == "match"`
    entries are processed; uncertain decisions are dropped (caller handles).
    """
    matches = [d for d in decisions if d.get("decision") == "match"]
    items = _hydrate(matches, cv_client)
    groups = _group(items)
    plans: list[ItemPlan] = []
    for group in groups:
        plans.extend(_plan_group(group))
    return plans


# ---- phase 1: hydrate ---------------------------------------------------


def _hydrate(matches: list[dict[str, Any]], cv) -> list[dict[str, Any]]:
    """For each decision, attach CV volume + issue metadata."""
    out = []
    for d in matches:
        out.append({
            "decision": d,
            "cv_volume": cv.get_volume(d["volume_id"]),
            "cv_issue": cv.get_issue(d["issue_id"]),
        })
    return out


# ---- phase 2: group -----------------------------------------------------


def _group(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cluster items into logical-series groups.

    Same volume_id always groups. Same (publisher, base_name) groups too,
    provided start_years are within SIBLING_YEAR_TOLERANCE.
    """
    # First pass: bucket by (publisher, base_name).
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for item in items:
        key = _coarse_key(item)
        buckets.setdefault(key, []).append(item)

    # Second pass: within each bucket, split if start_years span beyond
    # tolerance (likely a relaunch, not a sibling TPB run).
    groups: list[list[dict[str, Any]]] = []
    for bucket in buckets.values():
        groups.extend(_split_by_year_distance(bucket))
    return groups


def _coarse_key(item: dict[str, Any]) -> tuple:
    """(publisher, base_name) — base_name is the volume name with any
    `: Subtitle` suffix stripped."""
    vol = item["cv_volume"]
    publisher = (vol.get("publisher") or "").strip().lower()
    name = (vol.get("name") or "").strip()
    base = name.split(":", 1)[0].strip().lower()
    return (publisher, base)


def _split_by_year_distance(
    bucket: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group items whose CV start_years cluster within SIBLING_YEAR_TOLERANCE.
    Items separated by larger gaps go into separate groups."""
    if len(bucket) <= 1:
        return [bucket]
    # Sort by start_year, then walk grouping while years are within tolerance
    # of the running anchor (= earliest year in current cluster).
    sorted_items = sorted(
        bucket,
        key=lambda x: _start_year(x) or 0,
    )
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    cluster_anchor: int | None = None
    for it in sorted_items:
        y = _start_year(it)
        if cluster_anchor is None or (y is not None and y - cluster_anchor <= SIBLING_YEAR_TOLERANCE):
            current.append(it)
            if cluster_anchor is None and y is not None:
                cluster_anchor = y
        else:
            clusters.append(current)
            current = [it]
            cluster_anchor = y
    if current:
        clusters.append(current)
    return clusters


def _start_year(item: dict[str, Any]) -> int | None:
    y = item["cv_volume"].get("start_year")
    try:
        return int(y) if y is not None else None
    except (TypeError, ValueError):
        return None


# ---- phase 3: plan ------------------------------------------------------


def _plan_group(group: list[dict[str, Any]]) -> list[ItemPlan]:
    """Compute canonical metadata for every item in a group."""
    canonical_series = _canonical_series_name(group)
    canonical_volume = _canonical_volume_year(group)

    # Stable order for number assignment: by start_year, then issue_number.
    ordered = sorted(
        group,
        key=lambda x: (
            _start_year(x) or 0,
            _issue_number_int(x) or 0,
        ),
    )

    # Number assignment: prefer CV issue numbers when unique, else synthesize.
    cv_numbers = [_issue_number_int(it) for it in ordered]
    numbers: list[int]
    if all(n is not None for n in cv_numbers) and len(set(cv_numbers)) == len(cv_numbers):
        numbers = [n for n in cv_numbers]  # type: ignore[misc]
    else:
        numbers = list(range(1, len(ordered) + 1))

    # Detect annual-pattern (each volume is count_of_issues=1, multiple in
    # group) for Title synthesis fallback.
    annual_pattern = (
        len(group) > 1
        and all((it["cv_volume"].get("count_of_issues") or 0) == 1 for it in group)
    )

    plans: list[ItemPlan] = []
    for item, number in zip(ordered, numbers):
        vol = item["cv_volume"]
        issue = item["cv_issue"]
        decision = item["decision"]

        title = _resolve_title(
            issue=issue,
            volume=vol,
            number=number,
            annual_pattern=annual_pattern,
        )
        year = _issue_year(issue) or _start_year(item)

        plans.append(ItemPlan(
            filename=decision["filename"],
            issue_id=int(decision["issue_id"]),
            volume_id=int(decision["volume_id"]),
            series=canonical_series,
            volume=int(canonical_volume) if canonical_volume is not None else 0,
            number=number,
            title=title,
            year=year,
        ))
    return plans


def _canonical_series_name(group: list[dict[str, Any]]) -> str:
    """Most common base name across the group; ties broken by first occurrence."""
    bases = []
    for it in group:
        name = (it["cv_volume"].get("name") or "").strip()
        bases.append(name.split(":", 1)[0].strip())
    return Counter(bases).most_common(1)[0][0]


def _canonical_volume_year(group: list[dict[str, Any]]) -> int | None:
    """Earliest start_year across the group's CV volumes."""
    years = [y for y in (_start_year(it) for it in group) if y is not None]
    return min(years) if years else None


def _issue_number_int(item: dict[str, Any]) -> int | None:
    raw = item["cv_issue"].get("issue_number")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _issue_year(issue: dict[str, Any]) -> str | None:
    """Extract YYYY from cover_date (preferred) or store_date."""
    for field in ("cover_date", "store_date"):
        cd = issue.get(field) or ""
        m = re.match(r"^(\d{4})", str(cd))
        if m:
            return m.group(1)
    return None


def _extract_subtitle(volume_name: str | None) -> str | None:
    """'C.O.W.L.: Principles of Power' → 'Principles of Power'"""
    if not volume_name or ":" not in volume_name:
        return None
    return volume_name.split(":", 1)[1].strip() or None


def _resolve_title(
    *,
    issue: dict[str, Any],
    volume: dict[str, Any],
    number: int,
    annual_pattern: bool,
) -> str:
    """Compute Title. Preserves subtitle when collapsing Series.

    Order of preference:
      1. CV issue.name if it already contains the subtitle (or no subtitle exists).
      2. CV issue.name + ": <subtitle>" if subtitle missing from issue.name.
      3. Synthesized "Volume N: <subtitle>" if issue.name absent.
      4. Synthesized "One-Shot <year>" for annual patterns with no subtitle.
      5. Empty string fallback.
    """
    issue_name = (issue.get("name") or "").strip()
    if issue_name.lower() in ("none", "null"):
        issue_name = ""
    subtitle = _extract_subtitle(volume.get("name"))

    if issue_name and subtitle:
        if subtitle.lower() in issue_name.lower():
            return issue_name
        return f"{issue_name}: {subtitle}"

    if issue_name:
        return issue_name

    if subtitle:
        return f"Volume {number}: {subtitle}"

    if annual_pattern:
        year = volume.get("start_year")
        if year:
            return f"One-Shot {year}"

    return ""
