"""Unit tests for apply.py.

Covers lane/path derivation, name normalization, year tolerance, the
applied-log record shape, and the ComicInfo.xml override pass. Bundle
planning logic lives in test_bundle_planner.py; comictagger orchestration
is exercised end-to-end against the live cluster.

Run: python -m unittest test_apply -v
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from apply import (
    LANE_CONFIG,
    YEAR_TOLERANCE,
    _append_applied,
    _derive_lane,
    _derive_library_root,
    _norm_name,
    _override_comicinfo,
    _read_jsonl,
    _safe_folder,
    _set_field,
    _years_close,
)
from bundle_planner import ItemPlan


class LaneDerivationTests(unittest.TestCase):
    def test_comics_lane(self):
        p = Path("/books/incoming/comics/_unmatched")
        self.assertEqual(_derive_lane(p), "comics")
        self.assertEqual(_derive_library_root(p), Path("/books/library/comics"))

    def test_manga_lane(self):
        p = Path("/books/incoming/manga/_unmatched")
        self.assertEqual(_derive_lane(p), "manga")
        self.assertEqual(_derive_library_root(p), Path("/books/library/manga"))

    def test_unknown_lane_rejected(self):
        with self.assertRaises(ValueError):
            _derive_lane(Path("/books/incoming/ebook/_unmatched"))

    def test_missing_unmatched_segment_rejected(self):
        with self.assertRaises(ValueError):
            _derive_lane(Path("/books/incoming/comics/something-else"))

    def test_missing_incoming_segment_rejected(self):
        with self.assertRaises(ValueError):
            _derive_lane(Path("/random/path/_unmatched"))

    def test_lane_keys_have_configs(self):
        for lane in ("comics", "manga"):
            self.assertIn(lane, LANE_CONFIG)
            self.assertIn("filename_template", LANE_CONFIG[lane])
            self.assertIn("folder_with_year", LANE_CONFIG[lane])


class NormNameTests(unittest.TestCase):
    def test_strips_trailing_year(self):
        self.assertEqual(_norm_name("Radiant Black (2021)"), "radiant black")

    def test_lowercases(self):
        self.assertEqual(_norm_name("RADIANT BLACK"), "radiant black")

    def test_handles_none_and_empty(self):
        self.assertEqual(_norm_name(None), "")
        self.assertEqual(_norm_name(""), "")


class SafeFolderTests(unittest.TestCase):
    def test_replaces_slash(self):
        self.assertEqual(_safe_folder("No/One"), "No-One")

    def test_strips_trailing_dots_spaces(self):
        self.assertEqual(_safe_folder("Series."), "Series")
        self.assertEqual(_safe_folder("  Series  "), "Series")


class YearsCloseTests(unittest.TestCase):
    def test_within_tolerance(self):
        self.assertTrue(_years_close(2021, 2022))
        self.assertTrue(_years_close(2022, 2022))

    def test_beyond_tolerance(self):
        self.assertFalse(_years_close(2021, 2025))

    def test_handles_strings(self):
        self.assertTrue(_years_close("2021", "2022"))

    def test_handles_garbage(self):
        self.assertFalse(_years_close("?", 2022))
        self.assertFalse(_years_close(None, 2022))

    def test_tolerance_constant_is_one(self):
        self.assertEqual(YEAR_TOLERANCE, 1)


class SetFieldTests(unittest.TestCase):
    def test_replaces_existing(self):
        xml = "<ComicInfo><Series>Old</Series></ComicInfo>"
        self.assertEqual(
            _set_field(xml, "Series", "New"),
            "<ComicInfo><Series>New</Series></ComicInfo>",
        )

    def test_inserts_when_absent(self):
        xml = "<ComicInfo></ComicInfo>"
        result = _set_field(xml, "Series", "New")
        self.assertIn("<Series>New</Series>", result)

    def test_only_first_occurrence(self):
        # Defensive: only one Series tag should be replaced even if duplicated.
        xml = "<r><Series>A</Series><Series>B</Series></r>"
        self.assertEqual(
            _set_field(xml, "Series", "X"),
            "<r><Series>X</Series><Series>B</Series></r>",
        )


class OverrideComicInfoTests(unittest.TestCase):
    def _make_cbz(self, td: Path, xml: str) -> Path:
        cbz = td / "test.cbz"
        with zipfile.ZipFile(cbz, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("ComicInfo.xml", xml)
            z.writestr("page1.jpg", b"\xff\xd8\xff\xe0fake-jpeg")
        return cbz

    def test_rewrites_series_volume_number_title(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            xml = (
                "<?xml version='1.0'?>\n"
                "<ComicInfo>"
                "<Series>Old: Subtitle</Series>"
                "<Volume>2015</Volume>"
                "<Number>1</Number>"
                "<Title>Volume 1</Title>"
                "</ComicInfo>"
            )
            cbz = self._make_cbz(td_path, xml)
            plan = ItemPlan(
                filename=cbz.name, issue_id=1, volume_id=2,
                series="C.O.W.L.", volume=2014, number=2,
                title="Volume 2: The Greater Good", year=2015,
            )
            _override_comicinfo(cbz, plan)

            with zipfile.ZipFile(cbz, "r") as z:
                got = z.read("ComicInfo.xml").decode("utf-8")
                # Non-ComicInfo entries preserved
                self.assertEqual(z.read("page1.jpg")[:4], b"\xff\xd8\xff\xe0")
            self.assertIn("<Series>C.O.W.L.</Series>", got)
            self.assertIn("<Volume>2014</Volume>", got)
            self.assertIn("<Number>2</Number>", got)
            self.assertIn("<Title>Volume 2: The Greater Good</Title>", got)

    def test_skips_title_when_plan_title_empty(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            xml = "<ComicInfo><Series>X</Series><Title>Keep me</Title></ComicInfo>"
            cbz = self._make_cbz(td_path, xml)
            plan = ItemPlan(
                filename=cbz.name, issue_id=1, volume_id=1,
                series="X", volume=2020, number=1, title="", year=2020,
            )
            _override_comicinfo(cbz, plan)
            with zipfile.ZipFile(cbz, "r") as z:
                got = z.read("ComicInfo.xml").decode("utf-8")
            self.assertIn("<Title>Keep me</Title>", got)

    def test_raises_when_no_comicinfo(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cbz = td_path / "test.cbz"
            with zipfile.ZipFile(cbz, "w") as z:
                z.writestr("page1.jpg", b"x")
            plan = ItemPlan(
                filename=cbz.name, issue_id=1, volume_id=1,
                series="X", volume=2020, number=1, title="", year=2020,
            )
            with self.assertRaises(RuntimeError):
                _override_comicinfo(cbz, plan)


class AppliedLogTests(unittest.TestCase):
    def test_append_records_planner_fields(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "src.applied.jsonl"
            plan = ItemPlan(
                filename="Foo.cbz", issue_id=100, volume_id=200,
                series="Foo", volume=2022, number=1,
                title="Volume 1: Subtitle", year=2022,
            )
            _append_applied(log, "src", plan, Path("/dest"))

            records = _read_jsonl(log)
            self.assertEqual(len(records), 1)
            r = records[0]
            self.assertEqual(r["filename"], "Foo.cbz")
            self.assertEqual(r["issue_id"], 100)
            self.assertEqual(r["series"], "Foo")
            self.assertEqual(r["volume"], 2022)
            self.assertEqual(r["number"], 1)
            self.assertEqual(r["title"], "Volume 1: Subtitle")
            self.assertIn("applied_at", r)

    def test_append_is_additive(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "src.applied.jsonl"
            for i in range(3):
                plan = ItemPlan(
                    filename=f"f{i}.cbz", issue_id=i, volume_id=0,
                    series="X", volume=2020, number=i + 1,
                    title="", year=2020,
                )
                _append_applied(log, "src", plan, Path("/dest"))
            self.assertEqual(len(_read_jsonl(log)), 3)

    def test_read_missing_returns_empty(self):
        self.assertEqual(_read_jsonl(Path("/nonexistent/path.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
