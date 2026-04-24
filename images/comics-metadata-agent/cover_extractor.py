"""Extract CBZ pages as Anthropic vision image blocks. Pages whose
base64-encoded payload would exceed the 5 MiB wire cap (~3.75 MiB raw)
are downscaled to JPEG via Pillow; under-limit pages pass through."""

from __future__ import annotations

import base64
import io
import sys
import zipfile
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Anthropic's 5 MiB cap applies to the base64-encoded payload on the wire,
# not the raw bytes. Base64 expands ~4/3, so max raw = limit * 3/4.
ANTHROPIC_IMAGE_MAX_BYTES = 5 * 1024 * 1024
RAW_SIZE_LIMIT = (ANTHROPIC_IMAGE_MAX_BYTES * 3) // 4
COMPRESSION_TARGET_BYTES = RAW_SIZE_LIMIT - 64 * 1024
MIN_COMPRESSION_SCALE = 0.1


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
    """Return pages as Anthropic `image` content blocks. Oversize pages
    are JPEG-recompressed to fit under the 5 MiB limit; pages that can't
    be compressed enough are skipped with a stderr warning."""
    pages = extract_pages(cbz_path, page_indices)
    out = []
    for data in pages:
        try:
            prepared, media_type = _prepare_for_vision(data)
        except _CompressionImpossible as e:
            print(
                f"cover_extractor: skipping oversized page from {cbz_path}: {e}",
                file=sys.stderr,
            )
            continue
        out.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(prepared).decode("ascii"),
            },
        })
    return out


class _CompressionImpossible(Exception):
    pass


def _prepare_for_vision(raw: bytes) -> tuple[bytes, str]:
    """Return (bytes, media_type). Pass-through if already under limit;
    otherwise decode + iteratively halve dimensions until re-encoded
    JPEG fits. Pillow is imported lazily so callers that never hit
    compression don't pay the import cost."""
    if len(raw) <= COMPRESSION_TARGET_BYTES:
        return raw, detect_mime_type(raw)

    from PIL import Image  # type: ignore

    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    width, height = img.size
    scale = 1.0
    while scale >= MIN_COMPRESSION_SCALE:
        target = img if scale == 1.0 else img.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.LANCZOS,
        )
        buf = io.BytesIO()
        target.save(buf, format="JPEG", quality=85, optimize=True)
        if buf.tell() <= COMPRESSION_TARGET_BYTES:
            return buf.getvalue(), "image/jpeg"
        scale *= 0.5

    raise _CompressionImpossible(
        f"could not compress {len(raw)} bytes under {COMPRESSION_TARGET_BYTES}"
    )


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
