#!/usr/bin/env python3
"""Reprocess: re-run agent + apply against an already-shelved bundle.

Use case: prompt rules or planner logic improved, and we want to re-derive
metadata for an existing bundle without re-downloading the source PDFs or
re-converting them. The library files are already correct CBZs; only their
metadata + canonical placement may need updating.

Staging is on a separate scratch PVC (typical workflow context), not the
library PVC. This isolates disk usage: a reprocess crash on a 25 GB bundle
can't fill the library; the scratch PVC is sized at provision time and
GC'd on workflow completion. The CLI accepts --staging-dir explicitly so
the operator (or workflow) chooses the path; default falls back to
`<library-root>/.reprocess/<source_id>` for direct CLI use without scratch.

Sequence:
  1. Read <decision-log-dir>/<source_id>.applied.jsonl to enumerate the
     (filename, destination_folder) pairs the bundle previously produced.
  2. COPY each file from its current library location to the staging dir.
     Originals untouched until apply has shelved the new versions
     successfully.
  3. Rotate <source_id>.jsonl to a timestamped backup; keep
     <source_id>.applied.jsonl intact so apply.py's idempotency layer
     can no-op on items whose canonical state is unchanged.
  4. Invoke agent.py against the staging dir to produce a new decision log
     under the current prompt rules.
  5. Invoke apply.py against the new decision log; apply moves files from
     staging to canonical destinations (no-op if destination already
     correct per applied log).
  6. Stale-original cleanup: any old applied-log entry whose canonical
     destination differs from the new one gets the old file removed.
  7. Cleanup empty source directories.

Idempotent. Safe to re-run. If reprocess crashes mid-flight, the library
originals are untouched and re-running picks up where it left off.

Usage:
    reprocess.py \\
        --source-id S4NqZxAkmRkKZmEt \\
        --lane comics \\
        --source-title "Humble Comics Bundle: Massive-Verse by Image Comics" \\
        --staging-dir /scratch/reprocess \\
        --decision-log-dir /books/library/.agent-decisions/comics \\
        --kavita-url http://kavita.books.svc.cluster.local:5000 \\
        --kavita-api-key-file /secret/kavita/credential \\
        --comicvine-api-key-file /secret/comicvine/credential \\
        --anthropic-credential-file /secret/anthropic/credential

Exit codes:
  0 = ran to completion
  1 = ran but at least one item failed
  2 = configuration / input error
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_VERSION = "0.3.1"

# Per-lane defaults. Library root is derivable from lane, same convention as
# apply.py / replan.py.
_LANE_LIBRARY_ROOT = {
    "comics": Path("/books/library/comics"),
    "manga": Path("/books/library/manga"),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.lane not in _LANE_LIBRARY_ROOT:
        print(f"ERROR: unsupported lane {args.lane!r}", file=sys.stderr)
        return 2

    library_root = args.library_root or _LANE_LIBRARY_ROOT[args.lane]
    if not library_root.is_dir():
        print(f"ERROR: library root does not exist: {library_root}", file=sys.stderr)
        return 2

    decision_log = args.decision_log_dir / f"{args.source_id}.jsonl"
    applied_log = args.decision_log_dir / f"{args.source_id}.applied.jsonl"
    if not applied_log.is_file():
        print(
            f"ERROR: applied log not found: {applied_log}\n"
            f"reprocess requires a prior successful apply for this source_id.",
            file=sys.stderr,
        )
        return 2

    applied_records = _read_jsonl(applied_log)
    if not applied_records:
        print(f"applied log is empty: {applied_log}; nothing to reprocess")
        return 0

    print(f"Source: {args.source_id}")
    print(f"Lane: {args.lane}")
    print(f"Library root: {library_root}")
    print(f"Applied log: {applied_log} ({len(applied_records)} items)")

    # Staging dir: explicit --staging-dir parents the per-source-id subdir.
    # Default fallback (direct-CLI usage without scratch PVC) parents under
    # the library, matching the original in-place design but typically
    # superseded by the workflow passing /scratch.
    staging_parent = (
        args.staging_dir if args.staging_dir is not None
        else library_root / ".reprocess"
    )
    staging_root = staging_parent / args.source_id
    if staging_root.exists():
        print(
            f"WARNING: staging dir already exists, removing: {staging_root}",
            file=sys.stderr,
        )
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True)

    # ---- phase 1: stage (copy) ------------------------------------------
    print()
    print(f"=== staging {len(applied_records)} files → {staging_root} ===")
    staged: list[tuple[dict[str, Any], Path]] = []
    missing: list[dict[str, Any]] = []
    for rec in applied_records:
        # The applied log's canonical filename was the file's name AT APPLY
        # time. After apply, the file may have been renamed by comictagger -r.
        # Walk the destination_folder to find the .cbz that actually exists
        # rather than trusting the applied log's filename verbatim.
        dest_folder = Path(rec["destination_folder"])
        candidate = _find_file_in_folder(dest_folder, rec["filename"])
        if candidate is None:
            print(
                f"  MISS {rec['filename']} not found under {dest_folder}",
                file=sys.stderr,
            )
            missing.append(rec)
            continue
        # Stage with the file's CURRENT on-disk name, not the applied-log
        # filename — agent + apply work from the staged filename, and we
        # want them to see what's actually there.
        staged_path = staging_root / candidate.name
        shutil.copy2(candidate, staged_path)
        staged.append((rec, candidate))
        print(f"  staged: {candidate.name}")

    if not staged:
        print("ERROR: no files could be staged; aborting", file=sys.stderr)
        shutil.rmtree(staging_root)
        return 2

    if missing:
        print(
            f"\n{len(missing)} item(s) missing on disk; reprocess will skip them."
        )

    # ---- phase 2: rotate decision log -----------------------------------
    if decision_log.is_file():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = decision_log.with_suffix(f".{ts}.jsonl.bak")
        decision_log.rename(backup)
        print(f"\nrotated decision log → {backup.name}")

    # ---- phase 3: write source.json + invoke agent ----------------------
    source_ctx = {
        "source_id": args.source_id,
        "source_title": args.source_title,
    }
    if args.inferred_publisher:
        source_ctx["inferred_publisher"] = args.inferred_publisher
    if args.inferred_year_range:
        try:
            lo, hi = args.inferred_year_range.split(",", 1)
            source_ctx["inferred_year_range"] = [int(lo.strip()), int(hi.strip())]
        except ValueError:
            print(
                f"WARNING: ignoring malformed --inferred-year-range "
                f"{args.inferred_year_range!r} (expected 'YYYY,YYYY')",
                file=sys.stderr,
            )
    source_ctx_path = staging_root.parent / f"{args.source_id}.source.json"
    source_ctx_path.write_text(json.dumps(source_ctx, indent=2))

    print()
    print("=== invoking agent.py ===")
    agent_rc = subprocess.run(
        [
            sys.executable, "-B", "/app/agent.py",
            "--staging-dir", str(staging_root),
            "--lane", args.lane,
            "--source-context", str(source_ctx_path),
            "--decision-log-dir", str(args.decision_log_dir),
            "--kavita-url", args.kavita_url,
            "--kavita-api-key-file", str(args.kavita_api_key_file),
            "--comicvine-api-key-file", str(args.comicvine_api_key_file),
            "--anthropic-credential-file", str(args.anthropic_credential_file),
            "--model", args.model,
        ],
        check=False,
    )
    if agent_rc.returncode != 0:
        print(f"ERROR: agent.py exited {agent_rc.returncode}", file=sys.stderr)
        return 1

    # ---- phase 4: invoke apply ------------------------------------------
    print()
    print("=== invoking apply.py ===")
    apply_rc = subprocess.run(
        [
            sys.executable, "-B", "/app/apply.py",
            "--source-id", args.source_id,
            "--lane", args.lane,
            "--staging-dir", str(staging_root),
            "--library-root", str(library_root),
            "--decision-log-dir", str(args.decision_log_dir),
            "--kavita-url", args.kavita_url,
            "--kavita-api-key-file", str(args.kavita_api_key_file),
            "--comicvine-api-key-file", str(args.comicvine_api_key_file),
        ],
        check=False,
    )
    apply_failed = apply_rc.returncode != 0
    if apply_failed:
        print(
            f"WARNING: apply.py exited {apply_rc.returncode}; "
            f"library originals are still intact, staged copies remain.",
            file=sys.stderr,
        )

    # ---- phase 5: stale-original cleanup --------------------------------
    new_applied = _read_jsonl(applied_log)
    new_by_filename = {r["filename"]: r for r in new_applied}

    print()
    print("=== stale-original cleanup ===")
    affected_dirs: set[Path] = set()
    cleaned = 0
    for old_rec, old_path in staged:
        new_rec = new_by_filename.get(old_rec["filename"])
        if new_rec is None:
            # Item didn't get re-applied (agent flagged uncertain or apply
            # failed for this item). Leave the old file in place.
            continue
        new_dest_folder = Path(new_rec["destination_folder"])
        if new_dest_folder == Path(old_rec["destination_folder"]) and old_path.exists():
            # Same destination; apply may have rewritten in place. Nothing
            # to clean up at the old location.
            continue
        # Different destination — apply moved the staged copy to a new
        # canonical home. Remove the original from its old location.
        if old_path.exists():
            try:
                old_path.unlink()
                affected_dirs.add(old_path.parent)
                cleaned += 1
                print(f"  removed stale: {old_path}")
            except OSError as e:
                print(
                    f"  WARN failed to remove {old_path}: {e}",
                    file=sys.stderr,
                )

    # Cleanup empty old folders.
    for d in affected_dirs:
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                print(f"  rmdir empty: {d}")
        except OSError:
            pass

    # ---- phase 6: cleanup staging ---------------------------------------
    print()
    print(f"=== cleanup staging: {staging_root} ===")
    if staging_root.exists():
        # Anything left in staging means apply didn't claim it (no-op or
        # fail). Either way, our copy is no longer needed.
        leftover = list(staging_root.iterdir())
        for p in leftover:
            try:
                if p.is_file():
                    p.unlink()
                else:
                    shutil.rmtree(p)
            except OSError as e:
                print(f"  WARN failed to remove {p}: {e}", file=sys.stderr)
        try:
            staging_root.rmdir()
        except OSError as e:
            print(f"  WARN failed to rmdir {staging_root}: {e}", file=sys.stderr)

    # Source-context file alongside staging dir.
    if source_ctx_path.exists():
        source_ctx_path.unlink()

    # If we used the default in-library fallback, try to clean up the
    # .reprocess parent dir when empty. With an explicit --staging-dir
    # (typically a scratch PVC root), leave the parent alone — the
    # workflow's scratch lifecycle owns that.
    if args.staging_dir is None:
        parent = staging_root.parent
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    print()
    print(
        f"Result: staged={len(staged)} stale_cleaned={cleaned} "
        f"missing={len(missing)} apply_failed={apply_failed}"
    )
    return 1 if apply_failed else 0


# ---- helpers -----------------------------------------------------------


def _find_file_in_folder(folder: Path, hint: str) -> Path | None:
    """Locate a CBZ in `folder`. Prefers exact-name match against the hint;
    falls back to the only .cbz when the folder contains exactly one."""
    if not folder.is_dir():
        return None
    exact = folder / hint
    if exact.is_file():
        return exact
    candidates = sorted(folder.glob("*.cbz"))
    if len(candidates) == 1:
        return candidates[0]
    # Multiple candidates and no exact match — try a substring fallback.
    base = Path(hint).stem.lower()
    for c in candidates:
        if base in c.stem.lower() or c.stem.lower() in base:
            return c
    return None


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


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--source-id", required=True,
                   help="Source identifier (matches existing decision log filename).")
    p.add_argument("--lane", required=True, choices=sorted(_LANE_LIBRARY_ROOT),
                   help="Library lane.")
    p.add_argument("--source-title", required=True,
                   help="Human-readable source title (rebuilds source.json for the agent).")
    p.add_argument("--inferred-publisher", default=None)
    p.add_argument("--inferred-year-range", default=None,
                   help="Comma-separated year range, e.g., '2021,2025'.")
    p.add_argument("--library-root", type=Path, default=None,
                   help="Override the default lane library root.")
    p.add_argument("--staging-dir", type=Path, default=None,
                   help="Parent directory for the per-source staging copy. "
                        "Workflow context: /scratch/reprocess (separate "
                        "scratch PVC). Default fallback for CLI use without "
                        "scratch: <library-root>/.reprocess.")
    p.add_argument("--decision-log-dir", required=True, type=Path)
    p.add_argument("--kavita-url",
                   default="http://kavita.books.svc.cluster.local:5000")
    p.add_argument("--kavita-api-key-file", required=True, type=Path)
    p.add_argument("--comicvine-api-key-file", required=True, type=Path)
    p.add_argument("--anthropic-credential-file", required=True, type=Path)
    p.add_argument("--model", default="claude-sonnet-4-6")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
