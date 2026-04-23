"""Extract cover-matching images from CBZ files for Anthropic vision input.

A CBZ is a plain ZIP of image files. Pages are ordered by sorted (lexicographic)
entry names — matches how every CBZ reader lays them out.

This module is deliberately stdlib-only: zipfile + base64. Easier to test,
easier to audit, no image-processing dependencies leak into the container.
"""

from __future__ import annotations

import base64
import zipfile
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class CoverExtractError(Exception):
    """Raised when the CBZ is unreadable or contains no image pages."""


def list_pages(cbz_path: Path) -> list[str]:
    """Return image entry names sorted in reading order."""
    with zipfile.ZipFile(cbz_path, "r") as zf:
        return sorted(
            name
            for name in zf.namelist()
            if _is_image_entry(name)
        )


def count_pages(cbz_path: Path) -> int:
    """Count image pages in a CBZ (excludes ComicInfo.xml, dir entries, etc)."""
    return len(list_pages(cbz_path))


def extract_pages(
    cbz_path: Path,
    page_indices: list[int] | None = None,
) -> list[bytes]:
    """Extract raw image bytes at the given page indices (0-indexed).

    Default: cover only ([0]). Out-of-range indices are silently skipped
    so callers can request [0, 5, 20] on a 3-page one-shot without error.
    """
    if page_indices is None:
        page_indices = [0]
    with zipfile.ZipFile(cbz_path, "r") as zf:
        pages = sorted(name for name in zf.namelist() if _is_image_entry(name))
        if not pages:
            raise CoverExtractError(f"{cbz_path}: no image entries found")
        out: list[bytes] = []
        for i in page_indices:
            if 0 <= i < len(pages):
                out.append(zf.read(pages[i]))
        return out


def extract_pages_as_anthropic_images(
    cbz_path: Path,
    page_indices: list[int] | None = None,
) -> list[dict]:
    """Return pages formatted as Anthropic messages API `image` blocks:

        {"type": "image", "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": "<base64>",
        }}

    Mix-and-match formats are handled per-page; MIME detected from magic
    bytes so the caller doesn't have to pre-sort.
    """
    pages = extract_pages(cbz_path, page_indices)
    out = []
    for data in pages:
        out.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": detect_mime_type(data),
                "data": base64.b64encode(data).decode("ascii"),
            },
        })
    return out


def detect_mime_type(page_bytes: bytes) -> str:
    """Identify image MIME type from magic bytes. Returns a fallback rather
    than raising so a weird-but-valid CBZ page can still be sent — Anthropic
    rejects unknown media types, which surfaces the problem clearly there."""
    if page_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if page_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if page_bytes.startswith(b"RIFF") and page_bytes[8:12] == b"WEBP":
        return "image/webp"
    if page_bytes.startswith(b"GIF87a") or page_bytes.startswith(b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def _is_image_entry(name: str) -> bool:
    """True if the ZIP entry is an image we should treat as a page.

    Filters out directory entries (trailing slash) and non-image files
    (ComicInfo.xml, metadata, hidden macOS resource forks like __MACOSX/).
    """
    if name.endswith("/"):
        return False
    if name.startswith("__MACOSX/") or "/._" in name or name.startswith("._"):
        return False
    return Path(name).suffix.lower() in IMAGE_EXTENSIONS
