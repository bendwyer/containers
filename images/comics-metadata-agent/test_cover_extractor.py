"""Unit tests for cover_extractor.

Run: python -m unittest test_cover_extractor -v
"""

import base64
import tempfile
import unittest
import zipfile
from pathlib import Path

from cover_extractor import (
    count_pages,
    detect_mime_type,
    extract_pages,
    extract_pages_as_anthropic_images,
    list_pages,
    CoverExtractError,
)


# Minimal valid image magic-byte headers. Content after magic doesn't need
# to be a real image for our purposes — we only care about MIME detection
# and zipfile round-tripping.
JPEG_MAGIC = b"\xff\xd8\xff\xe0\x00\x10JFIF"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBPVP8 "
GIF_MAGIC = b"GIF89a" + b"\x00" * 10


def _make_cbz(entries):
    """Create a temp CBZ with the given (name, bytes) entries.

    Returns the Path; caller is responsible for cleanup (via addCleanup).
    """
    td = tempfile.mkdtemp()
    cbz = Path(td) / "book.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return cbz


class ListPagesTests(unittest.TestCase):
    def test_sorted_lexicographically(self):
        cbz = _make_cbz([
            ("002.jpg", JPEG_MAGIC),
            ("001.jpg", JPEG_MAGIC),
            ("003.jpg", JPEG_MAGIC),
        ])
        self.assertEqual(
            list_pages(cbz),
            ["001.jpg", "002.jpg", "003.jpg"],
        )

    def test_ignores_non_image_files(self):
        cbz = _make_cbz([
            ("001.jpg", JPEG_MAGIC),
            ("ComicInfo.xml", b"<ComicInfo />"),
            ("metadata.txt", b"notes"),
            ("002.jpg", JPEG_MAGIC),
        ])
        self.assertEqual(list_pages(cbz), ["001.jpg", "002.jpg"])

    def test_ignores_directory_entries(self):
        # Some CBZ writers include explicit dir entries.
        cbz = _make_cbz([
            ("pages/", b""),
            ("pages/001.jpg", JPEG_MAGIC),
            ("pages/002.jpg", JPEG_MAGIC),
        ])
        self.assertEqual(list_pages(cbz), ["pages/001.jpg", "pages/002.jpg"])

    def test_ignores_macos_junk(self):
        # macOS "Archive Utility" inserts these; they're resource forks, not pages.
        cbz = _make_cbz([
            ("__MACOSX/001.jpg", JPEG_MAGIC),
            ("001.jpg", JPEG_MAGIC),
            ("._002.jpg", JPEG_MAGIC),
            ("002.jpg", JPEG_MAGIC),
            ("pages/._003.jpg", JPEG_MAGIC),
            ("pages/003.jpg", JPEG_MAGIC),
        ])
        self.assertEqual(
            list_pages(cbz),
            ["001.jpg", "002.jpg", "pages/003.jpg"],
        )

    def test_mixed_extensions_all_included(self):
        cbz = _make_cbz([
            ("01.jpg", JPEG_MAGIC),
            ("02.png", PNG_MAGIC),
            ("03.webp", WEBP_MAGIC),
        ])
        self.assertEqual(len(list_pages(cbz)), 3)


class CountPagesTests(unittest.TestCase):
    def test_counts_images_only(self):
        cbz = _make_cbz([
            ("001.jpg", JPEG_MAGIC),
            ("ComicInfo.xml", b"<x/>"),
            ("002.jpg", JPEG_MAGIC),
        ])
        self.assertEqual(count_pages(cbz), 2)


class ExtractPagesTests(unittest.TestCase):
    def test_default_returns_cover_only(self):
        cbz = _make_cbz([
            ("01.jpg", JPEG_MAGIC + b"cover"),
            ("02.jpg", JPEG_MAGIC + b"page2"),
            ("03.jpg", JPEG_MAGIC + b"page3"),
        ])
        pages = extract_pages(cbz)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0], JPEG_MAGIC + b"cover")

    def test_extracts_requested_indices(self):
        cbz = _make_cbz([
            ("01.jpg", JPEG_MAGIC + b"a"),
            ("02.jpg", JPEG_MAGIC + b"b"),
            ("03.jpg", JPEG_MAGIC + b"c"),
        ])
        pages = extract_pages(cbz, page_indices=[0, 2])
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0], JPEG_MAGIC + b"a")
        self.assertEqual(pages[1], JPEG_MAGIC + b"c")

    def test_out_of_range_indices_silently_skipped(self):
        # Short one-shot: only 2 pages, caller asks for [0, 5, 20].
        cbz = _make_cbz([
            ("01.jpg", JPEG_MAGIC),
            ("02.jpg", JPEG_MAGIC),
        ])
        pages = extract_pages(cbz, page_indices=[0, 5, 20])
        self.assertEqual(len(pages), 1)

    def test_empty_cbz_raises(self):
        cbz = _make_cbz([("ComicInfo.xml", b"<x/>")])
        with self.assertRaises(CoverExtractError):
            extract_pages(cbz)

    def test_negative_index_silently_skipped(self):
        cbz = _make_cbz([("01.jpg", JPEG_MAGIC)])
        pages = extract_pages(cbz, page_indices=[-1, 0])
        self.assertEqual(len(pages), 1)


class DetectMimeTests(unittest.TestCase):
    def test_jpeg(self):
        self.assertEqual(detect_mime_type(JPEG_MAGIC), "image/jpeg")

    def test_png(self):
        self.assertEqual(detect_mime_type(PNG_MAGIC), "image/png")

    def test_webp(self):
        self.assertEqual(detect_mime_type(WEBP_MAGIC), "image/webp")

    def test_gif(self):
        self.assertEqual(detect_mime_type(GIF_MAGIC), "image/gif")

    def test_unknown_returns_octet_stream(self):
        self.assertEqual(detect_mime_type(b"\x00\x00\x00\x00"), "application/octet-stream")


class AnthropicImagesTests(unittest.TestCase):
    def test_shape_matches_anthropic_spec(self):
        cbz = _make_cbz([
            ("01.jpg", JPEG_MAGIC + b"cover"),
            ("02.png", PNG_MAGIC + b"page2"),
        ])
        images = extract_pages_as_anthropic_images(cbz, page_indices=[0, 1])
        self.assertEqual(len(images), 2)
        # First page: JPEG
        first = images[0]
        self.assertEqual(first["type"], "image")
        self.assertEqual(first["source"]["type"], "base64")
        self.assertEqual(first["source"]["media_type"], "image/jpeg")
        # Round-trip decode
        decoded = base64.b64decode(first["source"]["data"])
        self.assertEqual(decoded, JPEG_MAGIC + b"cover")
        # Second page: PNG (MIME detected per-page)
        self.assertEqual(images[1]["source"]["media_type"], "image/png")


if __name__ == "__main__":
    unittest.main()
