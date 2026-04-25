"""Unit tests for reprocess.py helpers.

The full reprocess flow is integration-tested by running it end-to-end
against the cluster; these tests cover the pure helpers.

Run: python -m unittest test_reprocess -v
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from reprocess import (
    _LANE_LIBRARY_ROOT,
    _find_file_by_id,
    _read_jsonl,
)


def make_cbz(path: Path, web_url: str | None = None, notes: str | None = None) -> Path:
    """Create a minimal CBZ at `path` with optional <Web> and <Notes> fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = ["<ComicInfo>"]
    if web_url:
        parts.append(f"<Web>{web_url}</Web>")
    if notes:
        parts.append(f"<Notes>{notes}</Notes>")
    parts.append("</ComicInfo>")
    xml = "".join(parts)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ComicInfo.xml", xml)
    return path


class FindFileByIdTests(unittest.TestCase):
    def test_returns_none_when_folder_missing(self):
        self.assertIsNone(
            _find_file_by_id(Path("/nonexistent"), "comicvine", 1)
        )

    def test_returns_none_when_folder_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(
                _find_file_by_id(Path(td), "comicvine", 1)
            )

    def test_finds_matching_cv_file_in_multi_file_folder(self):
        # Reproduces the original Massive-Verse failure: 7 CBZs in one
        # folder, all valid Series, only one with the requested issue_id.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for n in range(1, 8):
                make_cbz(
                    root / f"Radiant Black (2021) #{n:03d}.cbz",
                    web_url=f"https://comicvine.gamespot.com/r/4000-{880000 + n}/",
                )
            target = make_cbz(
                root / "Radiant Black (2021) #003.cbz",
                web_url="https://comicvine.gamespot.com/r/4000-972584/",
            )
            got = _find_file_by_id(root, "comicvine", 972584)
            self.assertEqual(got, target)

    def test_distinguishes_by_source(self):
        # Same numeric id under different sources resolves to different files.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cv_file = make_cbz(
                root / "cv.cbz",
                web_url="https://comicvine.gamespot.com/r/4000-42/",
            )
            mb_file = make_cbz(
                root / "mb.cbz",
                web_url="https://mangabaka.dev/series/42",
            )
            self.assertEqual(_find_file_by_id(root, "comicvine", 42), cv_file)
            self.assertEqual(_find_file_by_id(root, "mangabaka", 42), mb_file)

    def test_falls_back_to_notes_when_web_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = make_cbz(
                root / "x.cbz",
                notes="Tagged with ComicTagger using info from Comic Vine. [Issue ID 12345]",
            )
            self.assertEqual(_find_file_by_id(root, "comicvine", 12345), target)

    def test_returns_none_when_no_file_matches(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            make_cbz(root / "a.cbz", web_url="https://comicvine.gamespot.com/r/4000-1/")
            make_cbz(root / "b.cbz", web_url="https://comicvine.gamespot.com/r/4000-2/")
            self.assertIsNone(_find_file_by_id(root, "comicvine", 999))

    def test_skips_files_without_comicinfo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad = root / "bad.cbz"
            with zipfile.ZipFile(bad, "w") as z:
                z.writestr("page1.jpg", b"x")
            target = make_cbz(
                root / "good.cbz",
                web_url="https://comicvine.gamespot.com/r/4000-7/",
            )
            self.assertEqual(_find_file_by_id(root, "comicvine", 7), target)


class ReadJsonlTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(_read_jsonl(Path("/nonexistent")), [])

    def test_empty_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.jsonl"
            p.write_text("")
            self.assertEqual(_read_jsonl(p), [])

    def test_skips_blank_lines(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.jsonl"
            p.write_text('{"a":1}\n\n{"b":2}\n')
            self.assertEqual(_read_jsonl(p), [{"a": 1}, {"b": 2}])


class LaneLibraryRootTests(unittest.TestCase):
    def test_known_lanes(self):
        self.assertEqual(
            _LANE_LIBRARY_ROOT["comics"], Path("/books/library/comics")
        )
        self.assertEqual(
            _LANE_LIBRARY_ROOT["manga"], Path("/books/library/manga")
        )


if __name__ == "__main__":
    unittest.main()
