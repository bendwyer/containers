#!/usr/bin/env python3
"""Classify humble bundle items into lanes (comics, manga, ebook) and pick
the preferred format file per item by size.

Emits one manifest per lane at <output-dir>/<lane>.txt, tab-separated,
one line per item:

    <relative_path>\t<format>

where <format> is one of {pdf, cbz, epub, mobi}.

Selection rule: within each item directory, pick the largest file among
the recognized extensions. Size acts as a proxy for quality/resolution.

Usage:
    humble_sorter.py --bundle-dir /scratch/sources/humble-bundle/<name> \
                     --bundle-title "Humble Comics Bundle: ... by ..." \
                     --output-dir /scratch/classified
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path


MANGA_TITLE_RE = re.compile(r"\b(Manga|Manhwa|Manhua)\b", re.IGNORECASE)
ITEM_MANGA_MARKER_RE = re.compile(r"\((Manga|Manhwa|Manhua)\)|:\s*(Manga|Manhwa|Manhua)\b", re.IGNORECASE)
EBOOK_BUNDLE_PREFIX = "Humble Book Bundle"
EBOOK_SIZE_THRESHOLD_MIB = 50

MANGA_PUBLISHERS = (
    "Kodansha",
    "VIZ",
    "Yen Press",
    "Seven Seas",
    "Kana",
    "Shonen Jump",
    "Shogakukan",
)

RELEVANT_EXTENSIONS = {".pdf", ".cbz", ".epub", ".mobi"}


def classify_bundle(title: str) -> str:
    """Determine the default lane for a bundle from its title.

    Precedence:
      1. Explicit manga keyword (Manga/Manhwa/Manhua) → manga.
      2. Known manga publisher substring in title → manga.
      3. `Humble Book Bundle` prefix → ebook.
      4. Default → comics.
    """
    if MANGA_TITLE_RE.search(title):
        return "manga"
    for publisher in MANGA_PUBLISHERS:
        if publisher in title:
            return "manga"
    if EBOOK_BUNDLE_PREFIX in title:
        return "ebook"
    return "comics"


def classify_item(filename: str, size_bytes: int, bundle_lane: str) -> str:
    """Determine the lane for a single item.

    Precedence:
      1. `(Manga)` / `: Manga` marker in filename → manga (overrides bundle).
      2. Bundle is manga → manga.
      3. Bundle is ebook AND size < 50 MiB → ebook.
         Bundle is ebook AND size >= 50 MiB → comics (size-veto;
         the bundle was mislabeled and this item is actually comics).
      4. Default → comics (covers comics bundles and fallthrough).
    """
    if ITEM_MANGA_MARKER_RE.search(filename):
        return "manga"
    if bundle_lane == "manga":
        return "manga"
    if bundle_lane == "ebook":
        size_mib = size_bytes / (1024 * 1024)
        if size_mib < EBOOK_SIZE_THRESHOLD_MIB:
            return "ebook"
        return "comics"
    return "comics"


def group_items_by_directory(bundle_dir: Path) -> dict:
    """Group all recognized files by their parent directory.

    Each unique parent dir under bundle_dir represents one "item" in the
    bundle. Files in the same dir are format variants of the same item.
    """
    groups = defaultdict(list)
    for f in bundle_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in RELEVANT_EXTENSIONS:
            groups[f.parent].append(f)
    return groups


def pick_preferred_file(files):
    """Pick the largest file from a list. Size ≈ quality/resolution."""
    return max(files, key=lambda f: f.stat().st_size)


def classify_bundle_items(bundle_dir: Path, bundle_title: str):
    """Yield (relative_path, format, lane) — one per item, preferred file only."""
    bundle_lane = classify_bundle(bundle_title)
    groups = group_items_by_directory(bundle_dir)
    for item_dir in sorted(groups.keys()):
        chosen = pick_preferred_file(groups[item_dir])
        rel = chosen.relative_to(bundle_dir)
        fmt = chosen.suffix.lower().lstrip(".")
        size = chosen.stat().st_size
        # Check both the directory name and file name for manga markers —
        # humble-cli can apply the `(Manga)` suffix to either.
        identifier = f"{item_dir.name} {chosen.name}"
        lane = classify_item(identifier, size, bundle_lane)
        yield rel, fmt, lane


def write_manifests(bundle_dir: Path, bundle_title: str, output_dir: Path) -> dict:
    """Classify the bundle and write per-lane manifests.

    Returns {lane: [(relative_path, format)]} for callers that want the
    in-memory view. Manifest file format: tab-separated `<path>\t<format>`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    buckets = {"comics": [], "manga": [], "ebook": []}
    for rel, fmt, lane in classify_bundle_items(bundle_dir, bundle_title):
        buckets[lane].append((str(rel), fmt))
    for lane, entries in buckets.items():
        manifest = output_dir / f"{lane}.txt"
        lines = [f"{path}\t{fmt}" for path, fmt in entries]
        content = "\n".join(lines) + ("\n" if lines else "")
        manifest.write_text(content)
    return buckets


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle-dir", required=True, type=Path,
                        help="Path containing the bundle's source files (recursively)")
    parser.add_argument("--bundle-title", required=True,
                        help="Full bundle title from humble-cli details")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="Directory to write per-lane manifest files")
    args = parser.parse_args(argv)

    if not args.bundle_dir.is_dir():
        print(f"ERROR: bundle-dir does not exist: {args.bundle_dir}", file=sys.stderr)
        return 1

    bundle_lane = classify_bundle(args.bundle_title)
    print(f"bundle_title: {args.bundle_title}")
    print(f"bundle_lane: {bundle_lane}")

    buckets = write_manifests(args.bundle_dir, args.bundle_title, args.output_dir)
    for lane, entries in buckets.items():
        by_fmt = defaultdict(int)
        for _, fmt in entries:
            by_fmt[fmt] += 1
        fmt_summary = ", ".join(f"{f}={n}" for f, n in sorted(by_fmt.items())) or "-"
        print(f"{lane}: {len(entries)} items  ({fmt_summary})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
