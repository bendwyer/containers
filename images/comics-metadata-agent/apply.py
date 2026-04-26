#!/usr/bin/env python3
"""Bundle-aware apply: replay the agent's decision log into the library.

Reads <decision-log-dir>/<source_id>.jsonl, plans canonical metadata across
the whole bundle (see bundle_planner.py), then for each item:

The --staging-dir argument is the directory containing the .cbz files to
apply. Workflow context: /scratch/incoming/<lane>. Oneshot/_unmatched
context: /books/incoming/<lane>/_unmatched.

  1. comictagger -s --id <issue_id> --cv-use-series-start-as-volume on the
     source file to write CV-derived ComicInfo.xml.
  2. Python ComicInfo.xml override: rewrite Series/Volume/Number/Title to
     the planner's canonical values (subtitle preserved, sequential numbers,
     synthesized Titles for annual one-shots).
  3. Resolve destination folder via Kavita lookup (name + canonical year)
     or construct from the planner's canonical Series + Volume.
  4. comictagger -r --tags-read cr --move into the destination folder.
  5. Append <source_id>.applied.jsonl checkpoint for idempotent re-runs.

Lane and library root come from --lane and (optional) --library-root.

Usage:
    apply.py \\
        --source-id S4NqZxAkmRkKZmEt \\
        --lane comics \\
        --staging-dir /books/incoming/comics/_unmatched \\
        --decision-log-dir /books/library/.agent-decisions/comics \\
        --kavita-url http://kavita.books.svc.cluster.local:5000 \\
        --kavita-api-key-file /secret/kavita/credential \\
        --comicvine-api-key-file /secret/comicvine/credential

Exit codes:
  0 = ran to completion (some matches may have failed; check stderr)
  1 = ran but at least one match failed to apply
  2 = configuration / input error
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from bundle_planner import ItemPlan, plan_bundle
from comicvine_client import ComicVineClient
from kavita_client import KavitaClient
from mangabaka_client import MangaBakaClient


_VERSION = "0.3.10"


# Per-lane file output conventions. The planner produces canonical
# Series/Volume/Number/Title; the lane decides folder shape and filename
# template that comictagger -r uses on top of those tags.
LANE_CONFIG: dict[str, dict[str, Any]] = {
    "comics": {
        "filename_template": "{series} ({year}) #{issue}",
        "folder_with_year": True,
    },
    "manga": {
        # `v{issue}` so Kavita's filename parser detects each file as a
        # volume rather than a chapter. ComicInfo <Volume> reinforces this.
        "filename_template": "{series} v{issue} ({year})",
        "folder_with_year": False,
    },
}

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

    lane = args.lane
    if lane not in LANE_CONFIG:
        print(
            f"ERROR: --lane must be one of {sorted(LANE_CONFIG)}; got {lane!r}",
            file=sys.stderr,
        )
        return 2

    library_root = args.library_root or Path(f"/books/library/{lane}")
    config = LANE_CONFIG[lane]
    print(f"Lane: {lane}")
    print(f"Source dir: {args.staging_dir}")
    print(f"Library root: {library_root}")
    print(f"Filename template: {config['filename_template']}")

    decision_log = args.decision_log_dir / f"{args.source_id}.jsonl"
    applied_log = args.decision_log_dir / f"{args.source_id}.applied.jsonl"
    if not decision_log.is_file():
        print(f"ERROR: decision log not found: {decision_log}", file=sys.stderr)
        return 2

    decisions = _read_jsonl(decision_log)

    accepted_levels = (
        {"high"} if args.min_confidence == "high" else {"high", "medium"}
    )
    eligible = [
        d for d in decisions
        if d.get("decision") == "match"
        and d.get("confidence", "high") in accepted_levels
    ]

    cv = ComicVineClient(
        comicvine_key, user_agent=f"comics-metadata-agent-apply/{_VERSION}"
    )
    mb = MangaBakaClient(user_agent=f"comics-metadata-agent-apply/{_VERSION}")
    plans = plan_bundle(eligible, cv, mb, lane=args.lane)

    applied_set = {r["filename"] for r in _read_jsonl(applied_log)}
    todo = [p for p in plans if p.filename not in applied_set]
    skipped_already_applied = len(plans) - len(todo)

    print(
        f"Decisions: total={len(decisions)} eligible={len(eligible)} "
        f"planned={len(plans)} already-applied={skipped_already_applied} "
        f"to-apply={len(todo)}"
    )

    if not todo:
        print("Nothing to do.")
        return 0

    kavita = KavitaClient(args.kavita_url, kavita_key)
    kavita.authenticate()

    applied_count = 0
    failed: list[tuple[str, str]] = []
    for plan in todo:
        src = args.staging_dir / plan.filename
        if not src.exists():
            print(f"FAIL {plan.filename}: source file missing", file=sys.stderr)
            failed.append((plan.filename, "source-missing"))
            continue
        try:
            dest_folder = _resolve_destination(plan, library_root, kavita, config)
            _apply_one(
                src, plan, dest_folder,
                config["filename_template"], comicvine_key, args.lane,
            )
            _append_applied(applied_log, args.source_id, plan, dest_folder)
            applied_count += 1
            print(
                f"OK   {plan.filename} -> {dest_folder}/  "
                f"(series={plan.series!r} vol={plan.volume} #{plan.number} "
                f"title={plan.title!r})"
            )
        except Exception as e:
            print(f"FAIL {plan.filename}: {type(e).__name__}: {e}", file=sys.stderr)
            failed.append((plan.filename, str(e)))

    print()
    print(f"Applied: {applied_count}/{len(todo)}  |  cv_calls={cv.call_count}")
    if failed:
        print(f"Failed: {len(failed)}", file=sys.stderr)
        for fn, why in failed:
            print(f"  {fn}: {why}", file=sys.stderr)
        return 1
    return 0


# ---- destination resolution --------------------------------------------


def _resolve_destination(
    plan: ItemPlan,
    library_root: Path,
    kavita: KavitaClient,
    config: dict[str, Any],
) -> Path:
    """Prefer existing Kavita series folder when name+year matches within
    YEAR_TOLERANCE; else construct from the planner's canonical values."""
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


def _get_kavita_folder(kavita: KavitaClient, series_id: int) -> str | None:
    r = requests.get(
        f"{kavita.base_url}/api/Series/{series_id}",
        headers=kavita._auth_headers(),
        timeout=15,
    )
    if r.status_code != 200:
        return None
    return (r.json() or {}).get("folderPath")


# ---- comictagger orchestration -----------------------------------------


def _apply_one(
    src: Path,
    plan: ItemPlan,
    dest_folder: Path,
    filename_template: str,
    cv_key: str,
    lane: str,
) -> None:
    """1) comictagger save → 2) python override ComicInfo.xml → 3) move.

    The save step uses the plan's source (`comicvine` or `mangabaka`) so
    comictagger fetches metadata from the right talker. ComicVine matches
    use --comicvine-key + --cv-use-series-start-as-volume; MangaBaka uses
    --mangabaka-use-series-start-as-volume + a per-source filter knob.
    """
    dest_folder.mkdir(parents=True, exist_ok=True)

    if plan.source == "mangabaka":
        save_args = [
            "comictagger", "--no-gui", "-s", "-o", "-f",
            "--id", str(plan.issue_id),
            "--tags-write", "cr",
            "--source", "mangabaka",
            "--mangabaka-age-filter", "pornographic",
            "--mangabaka-use-series-start-as-volume",
            str(src),
        ]
    else:
        save_args = [
            "comictagger", "--no-gui", "-s", "-o", "-f",
            "--id", str(plan.issue_id),
            "--tags-write", "cr",
            "--source", "comicvine",
            "--comicvine-key", cv_key,
            "--cv-use-series-start-as-volume",
            str(src),
        ]
    write = subprocess.run(save_args, check=False, capture_output=True, text=True)
    if write.returncode != 0:
        raise RuntimeError(
            f"comictagger save failed (source={plan.source}, rc={write.returncode}): "
            f"{(write.stderr or write.stdout).strip()[:500]}"
        )

    _override_comicinfo(src, plan, lane)

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


def _override_comicinfo(cbz_path: Path, plan: ItemPlan, lane: str) -> None:
    """Rewrite ComicInfo.xml to the planner's canonical values.

    lane=comics: standard Series/Volume(year)/Number/Title shape.
    lane=manga: each file is one whole volume — write <Volume> as the
    volume number (the planner's `number`), drop <Number> and <Title>,
    stamp Manga=Yes, normalize Publisher to MB-canonical form. Kavita's
    manga library reads <Volume> to display by volume rather than chapter.
    """
    with zipfile.ZipFile(cbz_path, "r") as zin:
        if "ComicInfo.xml" not in zin.namelist():
            raise RuntimeError(f"no ComicInfo.xml after comictagger save: {cbz_path}")
        xml = zin.read("ComicInfo.xml").decode("utf-8")

    new_xml = xml
    new_xml = _set_field(new_xml, "Series", plan.series)
    if lane == "manga":
        new_xml = _set_field(new_xml, "Volume", str(plan.number))
        new_xml = _remove_field(new_xml, "Number")
        new_xml = _remove_field(new_xml, "Title")
        new_xml = _set_field(new_xml, "Manga", "Yes")
        if plan.publisher:
            new_xml = _set_field(new_xml, "Publisher", plan.publisher)
        new_xml, change = _normalize_manga_publisher(new_xml)
        if change:
            print(f"  normalized publisher: {change[0]!r} → {change[1]!r}")
    else:
        new_xml = _set_field(new_xml, "Number", str(plan.number))
        new_xml = _set_field(new_xml, "Volume", str(plan.volume))
        if plan.title:
            new_xml = _set_field(new_xml, "Title", plan.title)
    if new_xml == xml:
        return

    tmp = cbz_path.with_suffix(cbz_path.suffix + ".tmp")
    with zipfile.ZipFile(cbz_path, "r") as zin, zipfile.ZipFile(
        tmp, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            data = (
                new_xml.encode("utf-8")
                if info.filename == "ComicInfo.xml"
                else zin.read(info.filename)
            )
            zout.writestr(info, data)
    shutil.move(str(tmp), str(cbz_path))


def _set_field(xml: str, tag: str, value: str) -> str:
    new_inner = f"<{tag}>{value}</{tag}>"
    if re.search(rf"<{tag}>[^<]*</{tag}>", xml):
        return re.sub(rf"<{tag}>[^<]*</{tag}>", new_inner, xml, count=1)
    return xml.replace("</ComicInfo>", f"  {new_inner}\n</ComicInfo>")


def _remove_field(xml: str, tag: str) -> str:
    """Strip a top-level <Tag>...</Tag> entry plus its trailing whitespace.
    No-op if absent."""
    return re.sub(rf"\s*<{tag}>[^<]*</{tag}>", "", xml, count=1)


# Per-publisher canonicalization for manga lane. Each entry is
# (regex, canonical_name): a case-insensitive regex matched against the
# existing <Publisher> value, mapping known variants across CV/OPF/MB to
# the label MB uses (since we prefer MB for manga matching).
#
# Add entries only when a real variant has been observed in the wild.
# Patterns must NOT match the upstream Japanese parent entity (e.g., bare
# "Kodansha" is the Original publisher, not the English imprint we're
# unifying).
PUBLISHER_CANONICAL: list[tuple[re.Pattern, str]] = [
    # Kodansha imprint variants seen across CV ("Kodansha Comics USA"),
    # Kobo OPF ("Kodansha Comics"), and MB ("Kodansha Manga"). Excludes
    # bare "Kodansha" which is the Japanese original parent.
    (
        re.compile(r"^\s*Kodansha\s+(?:Comics(?:\s+USA)?|Manga)\s*$", re.IGNORECASE),
        "Kodansha Manga",
    ),
]


def _normalize_manga_publisher(
    xml: str,
) -> tuple[str, tuple[str, str] | None]:
    """If <Publisher> matches a known variant pattern, rewrite it to the
    canonical form. Returns (new_xml, (old, new)) when a rewrite happened;
    (xml, None) otherwise. Caller prints the change for traceability."""
    m = re.search(r"<Publisher>([^<]+)</Publisher>", xml)
    if not m:
        return xml, None
    current = m.group(1).strip()
    for pattern, canonical in PUBLISHER_CANONICAL:
        if pattern.match(current) and current != canonical:
            return _set_field(xml, "Publisher", canonical), (current, canonical)
    return xml, None


# ---- applied log -------------------------------------------------------


def _append_applied(
    log_path: Path,
    source_id: str,
    plan: ItemPlan,
    dest_folder: Path,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "filename": plan.filename,
        "issue_id": plan.issue_id,
        "volume_id": plan.volume_id,
        "series": plan.series,
        "volume": plan.volume,
        "number": plan.number,
        "title": plan.title,
        "metadata_source": plan.source,
        "destination_folder": str(dest_folder),
        "source_id": source_id,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---- helpers -----------------------------------------------------------


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
    return _YEAR_SUFFIX.sub("", (name or "")).strip().lower()


def _safe_folder(name: str | None) -> str:
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
    p.add_argument("--source-id", required=True)
    p.add_argument("--lane", required=True, choices=sorted(LANE_CONFIG),
                   help="Library lane the items belong in. Determines filename "
                        "template and folder convention.")
    p.add_argument("--staging-dir", required=True, type=Path,
                   help="Directory containing the .cbz files awaiting apply. "
                        "Workflow context: /scratch/incoming/<lane>. Oneshot "
                        "context: /books/incoming/<lane>/_unmatched.")
    p.add_argument("--library-root", type=Path, default=None,
                   help="Where canonical files land. Defaults to "
                        "/books/library/<lane>.")
    p.add_argument("--decision-log-dir", required=True, type=Path)
    p.add_argument("--kavita-url",
                   default="http://kavita.books.svc.cluster.local:5000")
    p.add_argument("--kavita-api-key-file", required=True, type=Path)
    p.add_argument("--comicvine-api-key-file", required=True, type=Path)
    p.add_argument("--min-confidence", choices=["high", "medium"], default="high",
                   help="Apply only matches at or above this confidence level.")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
