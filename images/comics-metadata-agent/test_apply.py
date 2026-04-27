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
    _norm_name,
    _override_comicinfo,
    _parse_args,
    _read_jsonl,
    _safe_folder,
    _set_field,
    _years_close,
)
from bundle_planner import ItemPlan


class CliArgsTests(unittest.TestCase):
    def _parse(self, *extra):
        argv = [
            "--source-id", "X",
            "--lane", "comics",
            "--staging-dir", "/scratch/incoming/comics",
            "--decision-log-dir", "/books/library/.agent-decisions/comics",
            "--kavita-api-key-file", "/secret/kavita/credential",
            "--comicvine-api-key-file", "/secret/comicvine/credential",
            *extra,
        ]
        return _parse_args(argv)

    def test_default_library_root_derives_from_lane(self):
        # When --library-root not provided, main() defaults to
        # /books/library/<lane>. Argparse sets it to None by default.
        args = self._parse()
        self.assertIsNone(args.library_root)
        self.assertEqual(args.lane, "comics")

    def test_explicit_library_root_overrides_default(self):
        args = self._parse("--library-root", "/custom/path")
        self.assertEqual(args.library_root, Path("/custom/path"))

    def test_lane_required(self):
        with self.assertRaises(SystemExit):
            _parse_args([
                "--source-id", "X",
                "--staging-dir", "/x",
                "--decision-log-dir", "/y",
                "--kavita-api-key-file", "/k",
                "--comicvine-api-key-file", "/c",
            ])

    def test_lane_must_be_supported(self):
        with self.assertRaises(SystemExit):
            self._parse("--lane", "ebook")  # not in LANE_CONFIG

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

    def test_escapes_xml_significant_characters(self):
        # Regression: MangaBaka publisher joins like
        # "Creek & River Co., Ltd, Dark Horse Manga" otherwise emit a raw
        # `&` in <Publisher>, which makes the comictagger -r read step fail
        # because comicapi's parser drops all metadata on ParseError. The
        # rewritten XML must round-trip cleanly through ElementTree.
        import xml.etree.ElementTree as ET
        xml = "<ComicInfo><Publisher>Old</Publisher></ComicInfo>"
        result = _set_field(xml, "Publisher", "Creek & River Co., Ltd")
        self.assertIn(
            "<Publisher>Creek &amp; River Co., Ltd</Publisher>", result
        )
        ET.fromstring(result)  # raises ParseError if escaping is wrong


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
            _override_comicinfo(cbz, plan, "comics")

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
            _override_comicinfo(cbz, plan, "comics")
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
                _override_comicinfo(cbz, plan, "comics")

    def test_manga_lane_writes_volume_drops_number_and_title(self):
        # Manga: Kavita reads <Volume> for volume display; <Number> would
        # show as a chapter sub-unit, which we don't want for whole-volume
        # files. Title is suppressed regardless.
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            xml = (
                "<?xml version='1.0'?>\n"
                "<ComicInfo>"
                "<Series>Old</Series>"
                "<Volume>2024</Volume>"
                "<Number>1</Number>"
                "<Title>Volume 1</Title>"
                "</ComicInfo>"
            )
            cbz = self._make_cbz(td_path, xml)
            plan = ItemPlan(
                filename=cbz.name, issue_id=139, volume_id=139,
                series="Shangri-La Frontier", volume=2020, number=1,
                title="Volume 1", year=2020,
                source="mangabaka", publisher="Kodansha Manga",
            )
            _override_comicinfo(cbz, plan, "manga")

            with zipfile.ZipFile(cbz, "r") as z:
                got = z.read("ComicInfo.xml").decode("utf-8")
            self.assertIn("<Series>Shangri-La Frontier</Series>", got)
            self.assertIn("<Volume>1</Volume>", got)
            self.assertNotIn("<Number>", got)
            self.assertNotIn("<Title>", got)
            self.assertIn("<Manga>Yes</Manga>", got)
            self.assertIn("<Publisher>Kodansha Manga</Publisher>", got)


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
