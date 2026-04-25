"""Unit tests for reprocess.py helpers.

The full reprocess flow is integration-tested by running it end-to-end
against the cluster; these tests cover the pure helpers.

Run: python -m unittest test_reprocess -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from reprocess import (
    _LANE_LIBRARY_ROOT,
    _find_file_in_folder,
    _read_jsonl,
)


class FindFileInFolderTests(unittest.TestCase):
    def _touch_cbz(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("ComicInfo.xml", "<ComicInfo/>")
        return path

    def test_returns_none_when_folder_missing(self):
        self.assertIsNone(_find_file_in_folder(Path("/nonexistent"), "x.cbz"))

    def test_exact_name_match(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._touch_cbz(Path(td) / "Foo (2020) #001.cbz")
            self._touch_cbz(Path(td) / "Other (2020) #001.cbz")
            got = _find_file_in_folder(Path(td), "Foo (2020) #001.cbz")
            self.assertEqual(got, f)

    def test_falls_back_to_single_cbz(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._touch_cbz(Path(td) / "Renamed Canonical.cbz")
            # Hint doesn't match, but only one cbz in folder.
            got = _find_file_in_folder(Path(td), "Original.cbz")
            self.assertEqual(got, f)

    def test_substring_fallback_when_hint_overlaps_canonical(self):
        with tempfile.TemporaryDirectory() as td:
            target = self._touch_cbz(Path(td) / "The Long Series Title (2022) #001.cbz")
            self._touch_cbz(Path(td) / "Other Title (2020) #001.cbz")
            # Hint stem 'the long series title (2022) #001' is substring of
            # the target's stem; substring fallback finds it.
            got = _find_file_in_folder(
                Path(td), "The Long Series Title (2022) #001.cbz"
            )
            self.assertEqual(got, target)

    def test_multiple_candidates_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self._touch_cbz(Path(td) / "Foo (2020) #001.cbz")
            self._touch_cbz(Path(td) / "Bar (2020) #001.cbz")
            got = _find_file_in_folder(Path(td), "Baz unrelated.cbz")
            self.assertIsNone(got)

    def test_empty_folder_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(_find_file_in_folder(Path(td), "x.cbz"))


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
