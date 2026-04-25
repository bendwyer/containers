"""Unit tests for replan.py.

Covers issue-id extraction, current-tag reading, plan-comparison idempotency,
canonical filename generation, and the file-index walker. Heavy I/O (move,
override) reuses apply.py helpers already covered by test_apply.py.

Run: python -m unittest test_replan -v
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from bundle_planner import ItemPlan
from replan import (
    _build_issue_id_index,
    _canonical_filename,
    _extract_issue_id,
    _read_current_tags,
    _tags_match_plan,
)
from apply import LANE_CONFIG


class ExtractIssueIdTests(unittest.TestCase):
    def test_from_web_url(self):
        xml = (
            "<ComicInfo>"
            "<Web>https://comicvine.gamespot.com/foo/4000-972584/</Web>"
            "</ComicInfo>"
        )
        self.assertEqual(_extract_issue_id(xml), 972584)

    def test_from_notes_fallback(self):
        xml = (
            "<ComicInfo>"
            "<Notes>Tagged with ComicTagger using info from Comic Vine. "
            "[Issue ID 123456]</Notes>"
            "</ComicInfo>"
        )
        self.assertEqual(_extract_issue_id(xml), 123456)

    def test_web_takes_precedence_over_notes(self):
        xml = (
            "<ComicInfo>"
            "<Web>https://comicvine.gamespot.com/foo/4000-1/</Web>"
            "<Notes>[Issue ID 999]</Notes>"
            "</ComicInfo>"
        )
        self.assertEqual(_extract_issue_id(xml), 1)

    def test_returns_none_when_neither_present(self):
        xml = "<ComicInfo><Series>Foo</Series></ComicInfo>"
        self.assertIsNone(_extract_issue_id(xml))

    def test_returns_none_for_malformed_web(self):
        xml = "<ComicInfo><Web>not a cv url</Web></ComicInfo>"
        self.assertIsNone(_extract_issue_id(xml))


class TagsMatchPlanTests(unittest.TestCase):
    def _plan(self, **kwargs):
        defaults = {
            "filename": "x.cbz", "issue_id": 1, "volume_id": 1,
            "series": "Foo", "volume": 2022, "number": 1,
            "title": "Volume 1", "year": 2022,
        }
        defaults.update(kwargs)
        return ItemPlan(**defaults)

    def test_full_match(self):
        plan = self._plan()
        current = {"Series": "Foo", "Volume": "2022", "Number": "1", "Title": "Volume 1"}
        self.assertTrue(_tags_match_plan(current, plan))

    def test_series_mismatch(self):
        plan = self._plan()
        current = {"Series": "Bar", "Volume": "2022", "Number": "1", "Title": "Volume 1"}
        self.assertFalse(_tags_match_plan(current, plan))

    def test_volume_mismatch(self):
        plan = self._plan()
        current = {"Series": "Foo", "Volume": "2025", "Number": "1", "Title": "Volume 1"}
        self.assertFalse(_tags_match_plan(current, plan))

    def test_number_mismatch(self):
        plan = self._plan()
        current = {"Series": "Foo", "Volume": "2022", "Number": "5", "Title": "Volume 1"}
        self.assertFalse(_tags_match_plan(current, plan))

    def test_title_mismatch(self):
        plan = self._plan(title="Volume 1: Subtitle")
        current = {"Series": "Foo", "Volume": "2022", "Number": "1", "Title": "Volume 1"}
        self.assertFalse(_tags_match_plan(current, plan))

    def test_empty_plan_title_skips_check(self):
        # When planner has no Title preference, current's existing Title is OK.
        plan = self._plan(title="")
        current = {"Series": "Foo", "Volume": "2022", "Number": "1", "Title": "anything"}
        self.assertTrue(_tags_match_plan(current, plan))


class CanonicalFilenameTests(unittest.TestCase):
    def _plan(self, **kwargs):
        defaults = {
            "filename": "x.cbz", "issue_id": 1, "volume_id": 1,
            "series": "Foo", "volume": 2022, "number": 1,
            "title": "", "year": 2022,
        }
        defaults.update(kwargs)
        return ItemPlan(**defaults)

    def test_comics_template(self):
        plan = self._plan(series="Radiant Black", volume=2021, number=3, year=2022)
        self.assertEqual(
            _canonical_filename(plan, LANE_CONFIG["comics"]),
            "Radiant Black (2022) #003.cbz",
        )

    def test_manga_template(self):
        plan = self._plan(series="Foo Manga", volume=2020, number=2, year=2021)
        self.assertEqual(
            _canonical_filename(plan, LANE_CONFIG["manga"]),
            "Foo Manga #002 (2021).cbz",
        )

    def test_zero_pads_number(self):
        plan = self._plan(series="Foo", volume=2022, number=42, year=2022)
        self.assertIn("#042", _canonical_filename(plan, LANE_CONFIG["comics"]))

    def test_falls_back_to_volume_when_year_missing(self):
        plan = self._plan(series="Foo", volume=2022, number=1, year=None)
        self.assertEqual(
            _canonical_filename(plan, LANE_CONFIG["comics"]),
            "Foo (2022) #001.cbz",
        )

    def test_safe_for_fs_strips_slash(self):
        plan = self._plan(series="No/One", volume=2023, number=1, year=2023)
        self.assertEqual(
            _canonical_filename(plan, LANE_CONFIG["comics"]),
            "No-One (2023) #001.cbz",
        )


class FileIndexTests(unittest.TestCase):
    def _make_cbz(self, path: Path, issue_id: int | None):
        path.parent.mkdir(parents=True, exist_ok=True)
        web = (
            f"<Web>https://comicvine.gamespot.com/foo/4000-{issue_id}/</Web>"
            if issue_id is not None
            else ""
        )
        xml = f"<ComicInfo><Series>Foo</Series>{web}</ComicInfo>"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("ComicInfo.xml", xml)

    def test_walks_recursive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_cbz(root / "Foo (2020)" / "a.cbz", 100)
            self._make_cbz(root / "Bar (2021)" / "b.cbz", 200)
            self._make_cbz(root / "deep" / "nested" / "c.cbz", 300)

            index = _build_issue_id_index(root)
            self.assertEqual(set(index.keys()), {100, 200, 300})

    def test_skips_missing_comicinfo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cbz = root / "x.cbz"
            with zipfile.ZipFile(cbz, "w") as z:
                z.writestr("page1.jpg", b"x")
            self.assertEqual(_build_issue_id_index(root), {})

    def test_skips_unparseable_web(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_cbz(root / "x.cbz", None)
            self.assertEqual(_build_issue_id_index(root), {})


class ReadCurrentTagsTests(unittest.TestCase):
    def test_reads_all_fields(self):
        with tempfile.TemporaryDirectory() as td:
            cbz = Path(td) / "x.cbz"
            xml = (
                "<ComicInfo>"
                "<Series>Foo</Series>"
                "<Volume>2020</Volume>"
                "<Number>3</Number>"
                "<Title>Volume 3: Subtitle</Title>"
                "</ComicInfo>"
            )
            with zipfile.ZipFile(cbz, "w") as z:
                z.writestr("ComicInfo.xml", xml)

            tags = _read_current_tags(cbz)
            self.assertEqual(tags, {
                "Series": "Foo",
                "Volume": "2020",
                "Number": "3",
                "Title": "Volume 3: Subtitle",
            })

    def test_missing_fields_default_empty_string(self):
        with tempfile.TemporaryDirectory() as td:
            cbz = Path(td) / "x.cbz"
            with zipfile.ZipFile(cbz, "w") as z:
                z.writestr("ComicInfo.xml", "<ComicInfo><Series>Foo</Series></ComicInfo>")
            tags = _read_current_tags(cbz)
            self.assertEqual(tags["Series"], "Foo")
            self.assertEqual(tags["Volume"], "")
            self.assertEqual(tags["Title"], "")


if __name__ == "__main__":
    unittest.main()
