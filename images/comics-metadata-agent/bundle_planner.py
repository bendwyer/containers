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
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any

from comicvine_client import ComicVineAPIError
from mangabaka_client import MangaBakaAPIError


# Sibling start-year distance beyond which we treat same-base-name volumes
# as unrelated relaunches (e.g., Batman 2011 vs Batman 2016). Within this,
# they're considered the same logical series.
SIBLING_YEAR_TOLERANCE = 3


DEFAULT_SOURCE = "comicvine"


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
    source: str = DEFAULT_SOURCE


def plan_bundle(
    decisions: list[dict[str, Any]],
    cv_client,
    mb_client=None,
    lane: str = "comics",
) -> list[ItemPlan]:
    """Return a per-item plan for every match decision.

    `lane` governs name canonicalization: comics treats CV "Series:
    Subtitle" as a TPB pattern (subtitle moves to Title); manga preserves
    the full name (the colon suffix is part of edition identity, e.g.
    "Battle Angel Alita: Last Order Omnibus")."""
    matches = [d for d in decisions if d.get("decision") == "match"]
    items = _hydrate(matches, cv_client, mb_client)
    groups = _group(items)
    plans: list[ItemPlan] = []
    for group in groups:
        plans.extend(_plan_group(group, lane=lane))
    return plans


# ---- phase 1: hydrate ---------------------------------------------------


def _hydrate(
    matches: list[dict[str, Any]],
    cv,
    mb=None,
) -> list[dict[str, Any]]:
    """Attach per-source volume + issue metadata to each decision.

    For ComicVine: separate volume + issue records.
    For MangaBaka: a single series record acts as both (MB has no
    separate per-issue concept; volume_id == issue_id == series_id).
    """
    out = []
    for d in matches:
        source = d.get("source") or DEFAULT_SOURCE
        try:
            if source == "comicvine":
                volume = cv.get_volume(d["volume_id"])
                issue = cv.get_issue(d["issue_id"])
            elif source == "mangabaka":
                if mb is None:
                    raise ValueError(
                        "decision has source=mangabaka but no MangaBaka client provided"
                    )
                series = mb.get_series(d["volume_id"])
                volume = series
                issue = series  # same record; MB has no per-issue data
            else:
                raise ValueError(f"unknown decision source: {source!r}")
        except (ComicVineAPIError, MangaBakaAPIError) as e:
            # Bad id from the agent (most often issue_id / volume_id confusion)
            # would otherwise abort the whole bundle. Drop the item; the
            # workflow's staging sweep dead-letters its file for review.
            print(
                f"WARN dropping {d.get('filename')!r} from plan: "
                f"{source} hydrate failed "
                f"(volume_id={d.get('volume_id')}, issue_id={d.get('issue_id')}): "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )
            continue
        out.append({
            "decision": d,
            "source": source,
            "cv_volume": volume,
            "cv_issue": issue,
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
    """(source, publisher, base_name) — base_name is the volume name with
    any `: Subtitle` suffix stripped. Source is in the key so MangaBaka
    items never merge with ComicVine items even if they share a name; the
    metadata sources have separate id spaces."""
    source = item.get("source") or DEFAULT_SOURCE
    vol = item["cv_volume"]
    publisher = (vol.get("publisher") or "").strip().lower()
    name = (vol.get("name") or "").strip()
    base = name.split(":", 1)[0].strip().lower()
    return (source, publisher, base)


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


def _plan_group(
    group: list[dict[str, Any]],
    lane: str = "comics",
) -> list[ItemPlan]:
    """Compute canonical metadata for every item in a group."""
    canonical_series = _canonical_series_name(group, lane=lane)
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

    # Pass 1: resolve raw Titles per item.
    raw_titles = [
        _resolve_title(
            issue=it["cv_issue"],
            volume=it["cv_volume"],
            number=number,
            annual_pattern=annual_pattern,
        )
        for it, number in zip(ordered, numbers)
    ]

    # Pass 2: group-level Volume-width normalization. Detect dominant width
    # across all "Volume <N>" Titles in the group and renumber to that width
    # so a single CV outlier (e.g., "Volume 007" amid "Volume 1"–"Volume 6")
    # gets pulled in line.
    width = _detect_dominant_volume_width(raw_titles)

    plans: list[ItemPlan] = []
    for item, number, raw in zip(ordered, numbers, raw_titles):
        decision = item["decision"]

        # Per-item normalization: width → series-prefix strip → casing.
        title = raw
        if width is not None:
            title = _apply_volume_width(title, width)
        title = _strip_series_redundancy(title, canonical_series)
        title = _normalize_casing(title)

        year = _issue_year(item["cv_issue"]) or _start_year(item)

        plans.append(ItemPlan(
            filename=decision["filename"],
            issue_id=int(decision["issue_id"]),
            volume_id=int(decision["volume_id"]),
            series=canonical_series,
            volume=int(canonical_volume) if canonical_volume is not None else 0,
            number=number,
            title=title,
            year=year,
            source=item.get("source") or DEFAULT_SOURCE,
        ))
    return plans


def _canonical_series_name(
    group: list[dict[str, Any]],
    lane: str = "comics",
) -> str:
    """Most common base name across the group; ties broken by first occurrence.

    Comics lane / CV: splits on `:` so TPB-style names ("Series: Subtitle")
    yield the bare series, with subtitle going to Title. Manga lane (any
    source) and MangaBaka source: preserves the full name — the colon
    denotes a distinct edition or series ("Battle Angel Alita: Last Order
    Omnibus" is its own shelving unit, not a TPB of "Battle Angel Alita").
    """
    bases = []
    for it in group:
        name = (it["cv_volume"].get("name") or "").strip()
        source = it.get("source") or DEFAULT_SOURCE
        if lane == "manga" or source == "mangabaka":
            bases.append(name)
        else:
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


# ---- Title normalization (sibling-aware) --------------------------------


_VOLUME_NUM_RE = re.compile(r"^volume\s+(\d+)(.*)$", re.IGNORECASE)


def _detect_dominant_volume_width(titles: list[str]) -> int | None:
    """Most common digit-width across Titles matching '^Volume <N>'.

    Returns None when no Title matches. Used to pull outliers in line —
    e.g., 6 of 7 RB Titles are 'Volume 1'..'Volume 6' (width=1) and one is
    'Volume 007' (width=3); dominant=1, the outlier becomes 'Volume 7'.
    """
    widths: list[int] = []
    for t in titles:
        m = _VOLUME_NUM_RE.match(t)
        if m:
            widths.append(len(m.group(1)))
    if not widths:
        return None
    return Counter(widths).most_common(1)[0][0]


def _apply_volume_width(title: str, width: int) -> str:
    """Renumber a 'Volume <N>...' Title to fixed digit width. Preserves
    case-insensitively-matched 'Volume' as canonical 'Volume' on output."""
    m = _VOLUME_NUM_RE.match(title)
    if not m:
        return title
    n = int(m.group(1))
    return f"Volume {n:0{width}d}{m.group(2)}"


def _strip_series_redundancy(title: str, series: str) -> str:
    """Drop a redundant Series prefix from Title. Conservative: matches
    'Series: <rest>' or exact equality only — never bare-prefix to avoid
    eating legitimate sentence starts."""
    if not title or not series:
        return title
    t = title.strip()
    s = series.strip()
    if t.lower() == s.lower():
        return ""
    prefix = f"{s.lower()}: "
    if t.lower().startswith(prefix):
        return t[len(prefix):].strip()
    return title


def _normalize_casing(title: str) -> str:
    """Title-case Titles that are entirely uppercase. Skips short strings
    (likely acronyms) and anything with mixed case (already styled)."""
    if not title:
        return title
    has_alpha = any(c.isalpha() for c in title)
    if not has_alpha:
        return title
    if any(c.islower() for c in title):
        return title  # already mixed-case; trust CV
    if len(title) <= 8:
        return title  # short — likely an acronym (e.g., 'TPB', 'OGN')
    return title.title()
