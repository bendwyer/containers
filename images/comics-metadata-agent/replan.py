#!/usr/bin/env python3
"""Replan: re-apply bundle-aware planner output against already-shelved files.

Counterpart to apply.py. apply.py reads from `_unmatched/` and shelves into
the library; replan.py reads from the library itself and brings already-shelved
files in line with the current planner output. Use cases:

  - Planner improved (better grouping, Title rules) and you want existing
    bundles brought up to spec.
  - CV data refreshed and metadata should be re-derived.
  - Cleanup pass after a partial / older apply run.

Idempotent: if a file's current tags + location already match the plan, it's
a no-op. Files are located by issue_id extracted from ComicInfo.xml's <Web>
URL or <Notes> field.

Usage:
    replan.py \\
        --source-id S4NqZxAkmRkKZmEt \\
        --library-root /books/library/comics \\
        --decision-log-dir /books/library/.agent-decisions \\
        --kavita-url http://kavita.books.svc.cluster.local:5000 \\
        --kavita-api-key-file /secret/kavita/credential \\
        --comicvine-api-key-file /secret/comicvine/credential

Exit codes:
  0 = ran to completion
  1 = ran but at least one plan failed to apply
  2 = configuration / input error
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

# Reuse apply.py's helpers — they're stable and don't depend on apply-only flow.
from apply import (
    LANE_CONFIG,
    YEAR_TOLERANCE,
    _get_kavita_folder,
    _norm_name,
    _override_comicinfo,
    _read_jsonl,
    _read_secret,
    _safe_folder,
    _set_field,
    _years_close,
)
from bundle_planner import ItemPlan, plan_bundle
from comicvine_client import ComicVineClient
from kavita_client import KavitaClient
from mangabaka_client import MangaBakaClient


_VERSION = "0.3.2"


# ID is embedded in <Web> (most reliable) and <Notes> (fallback). Source is
# derived from which pattern matches: ComicVine URL is comicvine.gamespot.com
# with /4000-NNN/ permalink; MangaBaka URL is mangabaka.dev/series/NNN.
_CV_WEB_ID_RE = re.compile(r"/4000-(\d+)/")
_MB_WEB_ID_RE = re.compile(r"mangabaka\.dev/series/(\d+)")
_NOTES_ID_RE = re.compile(r"\[Issue ID (\d+)\]")
_NOTES_MB_HINT_RE = re.compile(r"MangaBaka", re.IGNORECASE)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        kavita_key = _read_secret(args.kavita_api_key_file)
        comicvine_key = _read_secret(args.comicvine_api_key_file)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    library_root = args.library_root
    if not library_root.is_dir():
        print(f"ERROR: library-root does not exist: {library_root}", file=sys.stderr)
        return 2

    lane = library_root.name
    if lane not in LANE_CONFIG:
        print(
            f"ERROR: --library-root must end in a known lane "
            f"({sorted(LANE_CONFIG)}); got {lane!r}",
            file=sys.stderr,
        )
        return 2
    config = LANE_CONFIG[lane]
    print(f"Lane: {lane}")
    print(f"Library root: {library_root}")

    decision_log = args.decision_log_dir / f"{args.source_id}.jsonl"
    if not decision_log.is_file():
        print(f"ERROR: decision log not found: {decision_log}", file=sys.stderr)
        return 2

    decisions = _read_jsonl(decision_log)
    eligible = [d for d in decisions if d.get("decision") == "match"]

    cv = ComicVineClient(
        comicvine_key, user_agent=f"comics-metadata-agent-replan/{_VERSION}"
    )
    mb = MangaBakaClient(user_agent=f"comics-metadata-agent-replan/{_VERSION}")
    plans = plan_bundle(eligible, cv, mb)
    print(f"Decisions: total={len(decisions)} eligible={len(eligible)} planned={len(plans)}")

    file_index = _build_issue_id_index(library_root)
    print(f"Library scan: indexed {len(file_index)} files by (source, issue_id)")

    kavita = KavitaClient(args.kavita_url, kavita_key)
    kavita.authenticate()

    n_noop = 0
    n_updated = 0
    n_moved = 0
    n_missing = 0
    failed: list[tuple[str, str]] = []
    affected_dirs: set[Path] = set()

    for plan in plans:
        cbz = file_index.get((plan.source, plan.issue_id))
        if cbz is None:
            print(
                f"MISS {plan.filename}  ({plan.source}, issue_id={plan.issue_id}) "
                f"not found in library"
            )
            n_missing += 1
            continue
        try:
            current = _read_current_tags(cbz)
            tags_match = _tags_match_plan(current, plan)
            dest_folder = _resolve_destination(plan, library_root, kavita, config)
            dest_filename = _canonical_filename(plan, config)
            dest_path = dest_folder / dest_filename
            location_match = (cbz == dest_path)

            if tags_match and location_match:
                print(f"OK   {cbz.relative_to(library_root)}  (canonical)")
                n_noop += 1
                continue

            if not tags_match:
                _override_comicinfo(cbz, plan)
                n_updated += 1

            if not location_match:
                affected_dirs.add(cbz.parent)
                dest_folder.mkdir(parents=True, exist_ok=True)
                if dest_path.exists() and dest_path != cbz:
                    raise RuntimeError(
                        f"target already exists: {dest_path}"
                    )
                shutil.move(str(cbz), str(dest_path))
                n_moved += 1
                print(
                    f"MOVE {plan.filename}  →  {dest_path.relative_to(library_root)}"
                )
            else:
                print(
                    f"TAGS {plan.filename}  (Series={plan.series!r} "
                    f"Vol={plan.volume} #{plan.number} Title={plan.title!r})"
                )
        except Exception as e:
            print(f"FAIL {plan.filename}: {type(e).__name__}: {e}", file=sys.stderr)
            failed.append((plan.filename, str(e)))

    # Cleanup folders that are now empty after moves.
    for d in affected_dirs:
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                print(f"RMDIR {d.relative_to(library_root)}")
        except Exception:
            pass  # Folder still has contents, or permissions blocked it.

    print()
    print(
        f"Result: noop={n_noop} updated={n_updated} moved={n_moved} "
        f"missing={n_missing} failed={len(failed)}  |  cv_calls={cv.call_count}"
    )
    if failed:
        for fn, why in failed:
            print(f"  {fn}: {why}", file=sys.stderr)
        return 1
    return 0


# ---- file location ------------------------------------------------------


def _build_issue_id_index(library_root: Path) -> dict[tuple[str, int], Path]:
    """Walk the library lane and index .cbz files by (source, issue_id).

    The source/id pair disambiguates: a numeric ID like 12345 could
    coincidentally exist in both ComicVine and MangaBaka's id spaces.
    """
    index: dict[tuple[str, int], Path] = {}
    for cbz in library_root.rglob("*.cbz"):
        try:
            with zipfile.ZipFile(cbz, "r") as z:
                if "ComicInfo.xml" not in z.namelist():
                    continue
                xml = z.read("ComicInfo.xml").decode("utf-8")
        except (zipfile.BadZipFile, OSError):
            continue
        key = _extract_source_and_id(xml)
        if key is not None:
            # First-wins; duplicates would mean two files claiming the same
            # source+id, which is itself a problem to surface — but rare.
            index.setdefault(key, cbz)
    return index


def _extract_source_and_id(xml: str) -> tuple[str, int] | None:
    """Pull (source, issue_id) from ComicInfo.xml.

    Source detection is Web-URL-based first (most reliable). Falls back to
    Notes parsing when Web doesn't match a known pattern; in that case the
    'MangaBaka' substring in Notes flips source to mangabaka, otherwise it
    defaults to comicvine.
    """
    web = re.search(r"<Web>([^<]+)</Web>", xml)
    if web:
        url = web.group(1)
        m = _CV_WEB_ID_RE.search(url)
        if m:
            return ("comicvine", int(m.group(1)))
        m = _MB_WEB_ID_RE.search(url)
        if m:
            return ("mangabaka", int(m.group(1)))
    notes = re.search(r"<Notes>([^<]+)</Notes>", xml)
    if notes:
        notes_text = notes.group(1)
        m = _NOTES_ID_RE.search(notes_text)
        if m:
            source = (
                "mangabaka" if _NOTES_MB_HINT_RE.search(notes_text) else "comicvine"
            )
            return (source, int(m.group(1)))
    return None


# ---- tag comparison + canonical paths ----------------------------------


def _read_current_tags(cbz: Path) -> dict[str, str]:
    with zipfile.ZipFile(cbz, "r") as z:
        xml = z.read("ComicInfo.xml").decode("utf-8")
    return {
        "Series": _field(xml, "Series"),
        "Volume": _field(xml, "Volume"),
        "Number": _field(xml, "Number"),
        "Title": _field(xml, "Title"),
    }


def _field(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml)
    return m.group(1).strip() if m else ""


def _tags_match_plan(current: dict[str, str], plan: ItemPlan) -> bool:
    """True if current tags already match the plan exactly."""
    if current["Series"] != plan.series:
        return False
    if current["Volume"] != str(plan.volume):
        return False
    if current["Number"] != str(plan.number):
        return False
    if plan.title and current["Title"] != plan.title:
        return False
    return True


def _resolve_destination(
    plan: ItemPlan,
    library_root: Path,
    kavita: KavitaClient,
    config: dict[str, Any],
) -> Path:
    """Same Kavita-aware resolution as apply.py — keep consistent."""
    hits = kavita.search_series(plan.series)
    series_norm = _norm_name(plan.series)
    exact = [h for h in hits if _norm_name(h.get("name")) == series_norm]
    chosen = None
    chosen_delta: int | None = None
    for h in exact:
        meta = kavita.get_series_metadata(h["series_id"])
        ky = meta.get("release_year")
        if ky and _years_close(plan.volume, ky):
            delta = abs(int(plan.volume) - int(ky))
            if chosen_delta is None or delta < chosen_delta:
                chosen = h
                chosen_delta = delta
    if chosen:
        folder = _get_kavita_folder(kavita, chosen["series_id"])
        if folder:
            return Path(folder)
    safe = _safe_folder(plan.series)
    if config["folder_with_year"]:
        return library_root / f"{safe} ({plan.volume})"
    return library_root / safe


def _canonical_filename(plan: ItemPlan, config: dict[str, Any]) -> str:
    """Render the canonical filename from the plan, mirroring comictagger's
    template substitution. No leading folder segment."""
    template: str = config["filename_template"]
    number_str = f"{plan.number:03d}"
    name = template.format(
        series=plan.series,
        year=plan.year if plan.year is not None else plan.volume,
        issue=number_str,
    )
    return _safe_folder(name) + ".cbz"


# ---- args --------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--source-id", required=True)
    p.add_argument("--library-root", required=True, type=Path,
                   help="Library lane root (e.g., /books/library/comics). "
                        "Lane is derived from the directory name.")
    p.add_argument("--decision-log-dir", required=True, type=Path)
    p.add_argument("--kavita-url",
                   default="http://kavita.books.svc.cluster.local:5000")
    p.add_argument("--kavita-api-key-file", required=True, type=Path)
    p.add_argument("--comicvine-api-key-file", required=True, type=Path)
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
