#!/usr/bin/env python3
"""Replay agent decisions: write ComicInfo.xml + move files into the library.

Reads <decision-log-dir>/<source_id>.jsonl and applies each `match` decision:
  1. comictagger -s --id <issue_id> --cv-use-series-start-as-volume on the
     source file to refresh ComicInfo.xml with canonical Volume.
  2. comictagger -r --tags-read cr --move into the lane-appropriate folder.
  3. Append <source_id>.applied.jsonl checkpoint for idempotent re-runs.

Lane (comics vs manga) and library root are derived from --unmatched-dir.
Folder convention follows the lane (comics: "Series (year)", manga: "Series").

Usage:
    apply.py \\
        --source-id S4NqZxAkmRkKZmEt \\
        --unmatched-dir /books/incoming/comics/_unmatched \\
        --decision-log-dir /books/library/.agent-decisions \\
        --kavita-url http://kavita.books.svc.cluster.local:5000 \\
        --kavita-api-key-file /secret/kavita/credential \\
        --comicvine-api-key-file /secret/comicvine/credential

Exit codes:
  0 = ran to completion (some matches may have failed; check stderr)
  1 = ran but at least one match failed to apply
  2 = configuration / input error (missing files, bad path, no decisions)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from comicvine_client import ComicVineClient
from kavita_client import KavitaClient


_VERSION = "0.1.6"


# Per-lane output conventions. Filename template is fed to comictagger's
# -r --template flag and uses tags written in the previous -s step. Folder
# pattern is computed in Python from CV volume metadata and passed via --dir,
# so the template handles only the filename portion.
LANE_CONFIG: dict[str, dict[str, Any]] = {
    "comics": {
        "filename_template": "{series} ({year}) #{issue}",
        "folder_with_year": True,
    },
    "manga": {
        "filename_template": "{series} #{issue} ({year})",
        "folder_with_year": False,
    },
}

# Tolerance for matching CV start_year to Kavita release_year — comictagger
# can drift ±1 between solicit/cover-date conventions. Beyond that we treat
# the Kavita series as a different volume entirely.
YEAR_TOLERANCE = 1

_YEAR_SUFFIX = re.compile(r"\s*\(\d{4}\)\s*$")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        kavita_key = _read_secret(args.kavita_api_key_file)
        comicvine_key = _read_secret(args.comicvine_api_key_file)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        lane = _derive_lane(args.unmatched_dir)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    library_root = _derive_library_root(args.unmatched_dir)
    config = LANE_CONFIG[lane]
    print(f"Lane: {lane}")
    print(f"Library root: {library_root}")
    print(f"Filename template: {config['filename_template']}")

    decision_log = args.decision_log_dir / f"{args.source_id}.jsonl"
    applied_log = args.decision_log_dir / f"{args.source_id}.applied.jsonl"
    if not decision_log.is_file():
        print(f"ERROR: decision log not found: {decision_log}", file=sys.stderr)
        return 2

    decisions = _read_jsonl(decision_log)
    matches = [d for d in decisions if d.get("decision") == "match"]
    if not matches:
        print("No match decisions to apply.")
        return 0

    accepted_levels = (
        {"high"} if args.min_confidence == "high" else {"high", "medium"}
    )
    gated = [d for d in matches if d.get("confidence", "high") in accepted_levels]
    skipped_for_confidence = len(matches) - len(gated)

    applied_set = {r["filename"] for r in _read_jsonl(applied_log)}
    todo = [d for d in gated if d["filename"] not in applied_set]
    skipped_already_applied = len(gated) - len(todo)

    print(
        f"Decisions: total={len(decisions)} match={len(matches)} "
        f"below-{args.min_confidence}={skipped_for_confidence} "
        f"already-applied={skipped_already_applied} to-apply={len(todo)}"
    )

    if not todo:
        print("Nothing to do.")
        return 0

    kavita = KavitaClient(args.kavita_url, kavita_key)
    cv = ComicVineClient(
        comicvine_key, user_agent=f"comics-metadata-agent-apply/{_VERSION}"
    )
    kavita.authenticate()

    applied_count = 0
    failed: list[tuple[str, str]] = []
    for d in todo:
        filename = d["filename"]
        src = args.unmatched_dir / filename
        if not src.exists():
            print(f"FAIL {filename}: source file missing", file=sys.stderr)
            failed.append((filename, "source-missing"))
            continue
        try:
            dest_folder = _resolve_destination(d, library_root, kavita, cv, config)
            _apply_one(
                src,
                d["issue_id"],
                dest_folder,
                config["filename_template"],
                comicvine_key,
            )
            _append_applied(applied_log, args.source_id, d, dest_folder)
            applied_count += 1
            print(f"OK   {filename} -> {dest_folder}/")
        except Exception as e:
            print(f"FAIL {filename}: {type(e).__name__}: {e}", file=sys.stderr)
            failed.append((filename, str(e)))

    print()
    print(f"Applied: {applied_count}/{len(todo)}  |  cv_calls={cv.call_count}")
    if failed:
        print(f"Failed: {len(failed)}", file=sys.stderr)
        for fn, why in failed:
            print(f"  {fn}: {why}", file=sys.stderr)
        return 1
    return 0


# ---- lane derivation -----------------------------------------------------


def _derive_lane(unmatched_dir: Path) -> str:
    """Lane is the path segment between 'incoming' and '_unmatched'.

    Hard-fails on any non-conforming layout so we don't silently mis-route.
    """
    parts = list(unmatched_dir.parts)
    try:
        i = parts.index("incoming")
    except ValueError as e:
        raise ValueError(
            f"--unmatched-dir must contain 'incoming' segment: {unmatched_dir}"
        ) from e
    if i + 2 >= len(parts) or parts[i + 2] != "_unmatched":
        raise ValueError(
            f"--unmatched-dir must match /.../incoming/<lane>/_unmatched: {unmatched_dir}"
        )
    lane = parts[i + 1]
    if lane not in LANE_CONFIG:
        raise ValueError(
            f"unsupported lane {lane!r}; expected one of {sorted(LANE_CONFIG)}"
        )
    return lane


def _derive_library_root(unmatched_dir: Path) -> Path:
    """Mirror /<base>/incoming/<lane>/_unmatched -> /<base>/library/<lane>"""
    parts = list(unmatched_dir.parts)
    i = parts.index("incoming")
    lane = parts[i + 1]
    base = Path(*parts[:i])
    return base / "library" / lane


# ---- destination resolution ---------------------------------------------


def _resolve_destination(
    decision: dict[str, Any],
    library_root: Path,
    kavita: KavitaClient,
    cv: ComicVineClient,
    config: dict[str, Any],
) -> Path:
    """Return the absolute folder path for this decision.

    Prefer an existing Kavita series folder when its name matches and its
    release_year is within YEAR_TOLERANCE of CV start_year. Otherwise
    construct from the canonical CV volume name + start_year. Constructing
    fresh lands the file in a NEW folder rather than risking the wrong
    existing one when years disagree wildly.
    """
    vol = cv.get_volume(decision["volume_id"])
    vname = vol.get("name") or "?"
    vyear = vol.get("start_year") or "?"

    hits = kavita.search_series(vname)
    vname_norm = _norm_name(vname)
    exact = [h for h in hits if _norm_name(h.get("name")) == vname_norm]

    chosen = None
    chosen_delta = None
    for h in exact:
        meta = kavita.get_series_metadata(h["series_id"])
        ky = meta.get("release_year")
        if ky and _years_close(vyear, ky):
            delta = abs(int(vyear) - int(ky))
            if chosen_delta is None or delta < chosen_delta:
                chosen = h
                chosen_delta = delta

    if chosen:
        folder = _get_kavita_folder(kavita, chosen["series_id"])
        if folder:
            return Path(folder)

    safe = _safe_folder(vname)
    if config["folder_with_year"]:
        return library_root / f"{safe} ({vyear})"
    return library_root / safe


def _get_kavita_folder(kavita: KavitaClient, series_id: int) -> str | None:
    """Fetch the on-disk folder path for a Kavita series, or None on miss.

    Uses the Series detail endpoint, not /api/Series/metadata which omits
    folderPath. Treats any non-200 as 'no folder' rather than failing.
    """
    r = requests.get(
        f"{kavita.base_url}/api/Series/{series_id}",
        headers=kavita._auth_headers(),
        timeout=15,
    )
    if r.status_code != 200:
        return None
    return (r.json() or {}).get("folderPath")


# ---- comictagger orchestration ------------------------------------------


def _apply_one(
    src: Path,
    issue_id: int,
    dest_folder: Path,
    filename_template: str,
    cv_key: str,
) -> None:
    """Run comictagger -s (write tags) then -r --move to dest_folder.

    On comictagger save failure the source file is unmodified (per
    comictagger's contract), so a re-run will retry cleanly because the
    applied log only records on success.
    """
    dest_folder.mkdir(parents=True, exist_ok=True)

    write = subprocess.run(
        [
            "comictagger", "--no-gui", "-s", "-o", "-f",
            "--id", str(issue_id),
            "--tags-write", "cr",
            "--source", "comicvine",
            "--comicvine-key", cv_key,
            "--cv-use-series-start-as-volume",
            str(src),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if write.returncode != 0:
        raise RuntimeError(
            f"comictagger save failed (rc={write.returncode}): "
            f"{(write.stderr or write.stdout).strip()[:500]}"
        )

    move = subprocess.run(
        [
            "comictagger", "--no-gui", "-r",
            "--tags-read", "cr",
            "--move",
            "--dir", str(dest_folder),
            "--template", filename_template,
            str(src),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if move.returncode != 0:
        raise RuntimeError(
            f"comictagger move failed (rc={move.returncode}): "
            f"{(move.stderr or move.stdout).strip()[:500]}"
        )

    if src.exists():
        raise RuntimeError(
            f"comictagger reported success but source still present: {src}"
        )


# ---- applied log --------------------------------------------------------


def _append_applied(
    log_path: Path,
    source_id: str,
    decision: dict[str, Any],
    dest_folder: Path,
) -> None:
    """Append one record to <source_id>.applied.jsonl. Idempotent re-runs
    use this to skip already-applied filenames."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "filename": decision["filename"],
        "issue_id": decision["issue_id"],
        "volume_id": decision["volume_id"],
        "destination_folder": str(dest_folder),
        "source_id": source_id,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ---- helpers ------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _norm_name(name: str | None) -> str:
    """Strip trailing (YYYY) and lowercase. Kavita series names embed the
    year; ComicVine names don't."""
    return _YEAR_SUFFIX.sub("", (name or "")).strip().lower()


def _safe_folder(name: str | None) -> str:
    """Replace fs-unsafe chars so 'No/One' becomes 'No-One'."""
    return re.sub(r"[/\\]", "-", (name or "")).strip(" .")


def _years_close(a: Any, b: Any, tol: int = YEAR_TOLERANCE) -> bool:
    try:
        return abs(int(a) - int(b)) <= tol
    except (TypeError, ValueError):
        return False


def _read_secret(path: Path) -> str:
    return path.read_text().strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--source-id", required=True,
                   help="Source identifier (matches decision log filename).")
    p.add_argument("--unmatched-dir", required=True, type=Path,
                   help="Path like /books/incoming/<lane>/_unmatched. "
                        "Lane and library root are derived from this.")
    p.add_argument("--decision-log-dir", required=True, type=Path,
                   help="Where <source_id>.jsonl + .applied.jsonl live.")
    p.add_argument("--kavita-url",
                   default="http://kavita.books.svc.cluster.local:5000")
    p.add_argument("--kavita-api-key-file", required=True, type=Path)
    p.add_argument("--comicvine-api-key-file", required=True, type=Path)
    p.add_argument("--min-confidence", choices=["high", "medium"], default="high",
                   help="Apply only matches at or above this confidence level. "
                        "Below-confidence matches stay in _unmatched/ for review.")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
