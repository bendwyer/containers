"""Unit tests for apply.py.

Covers lane/path derivation, name normalization, year tolerance, destination
resolution, and the applied-log idempotency check. comictagger orchestration
is not covered here — that's exercised end-to-end against the live cluster.

Run: python -m unittest test_apply -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from apply import (
    LANE_CONFIG,
    YEAR_TOLERANCE,
    _append_applied,
    _derive_lane,
    _derive_library_root,
    _norm_name,
    _read_jsonl,
    _resolve_destination,
    _safe_folder,
    _years_close,
)


class LaneDerivationTests(unittest.TestCase):
    def test_comics_lane(self):
        p = Path("/books/incoming/comics/_unmatched")
        self.assertEqual(_derive_lane(p), "comics")
        self.assertEqual(
            _derive_library_root(p), Path("/books/library/comics")
        )

    def test_manga_lane(self):
        p = Path("/books/incoming/manga/_unmatched")
        self.assertEqual(_derive_lane(p), "manga")
        self.assertEqual(
            _derive_library_root(p), Path("/books/library/manga")
        )

    def test_trailing_slash_irrelevant(self):
        # Path normalizes trailing slashes.
        p = Path("/books/incoming/comics/_unmatched/")
        self.assertEqual(_derive_lane(p), "comics")

    def test_unknown_lane_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _derive_lane(Path("/books/incoming/ebook/_unmatched"))
        self.assertIn("unsupported lane", str(ctx.exception))

    def test_missing_unmatched_segment_rejected(self):
        with self.assertRaises(ValueError):
            _derive_lane(Path("/books/incoming/comics/something-else"))

    def test_missing_incoming_segment_rejected(self):
        with self.assertRaises(ValueError):
            _derive_lane(Path("/random/path/_unmatched"))

    def test_lane_keys_have_configs(self):
        # Catches drift if we add a new lane without wiring its config.
        for lane in ("comics", "manga"):
            self.assertIn(lane, LANE_CONFIG)
            self.assertIn("filename_template", LANE_CONFIG[lane])
            self.assertIn("folder_with_year", LANE_CONFIG[lane])


class NormNameTests(unittest.TestCase):
    def test_strips_trailing_year(self):
        self.assertEqual(_norm_name("Radiant Black (2021)"), "radiant black")
        self.assertEqual(_norm_name("Rogue Sun (2022)"), "rogue sun")

    def test_lowercases(self):
        self.assertEqual(_norm_name("RADIANT BLACK"), "radiant black")

    def test_handles_none_and_empty(self):
        self.assertEqual(_norm_name(None), "")
        self.assertEqual(_norm_name(""), "")
        self.assertEqual(_norm_name("   "), "")

    def test_year_inside_name_preserved(self):
        # "Star Wars (2020)" → "star wars". But "Star Wars Year One (1999)"
        # → "star wars year one". Embedded years that aren't a trailing
        # parenthesized year are kept.
        self.assertEqual(_norm_name("Foo 2022 Bar (2024)"), "foo 2022 bar")


class SafeFolderTests(unittest.TestCase):
    def test_replaces_slash(self):
        self.assertEqual(_safe_folder("No/One"), "No-One")
        self.assertEqual(_safe_folder("a\\b"), "a-b")

    def test_strips_trailing_dots_spaces(self):
        self.assertEqual(_safe_folder("Series."), "Series")
        self.assertEqual(_safe_folder("  Series  "), "Series")

    def test_passes_through_clean_names(self):
        self.assertEqual(_safe_folder("Radiant Black"), "Radiant Black")


class YearsCloseTests(unittest.TestCase):
    def test_within_tolerance(self):
        self.assertTrue(_years_close(2021, 2022))
        self.assertTrue(_years_close(2022, 2021))
        self.assertTrue(_years_close(2022, 2022))

    def test_beyond_tolerance(self):
        self.assertFalse(_years_close(2021, 2025))
        self.assertFalse(_years_close(2010, 2014))

    def test_handles_strings(self):
        self.assertTrue(_years_close("2021", "2022"))

    def test_handles_garbage(self):
        self.assertFalse(_years_close("?", 2022))
        self.assertFalse(_years_close(None, 2022))

    def test_tolerance_constant_is_one(self):
        # Encoded the ComicVine-vs-Kavita drift observation; document it.
        self.assertEqual(YEAR_TOLERANCE, 1)


class ResolveDestinationTests(unittest.TestCase):
    """Mocks Kavita + CV to verify destination logic in isolation."""

    def setUp(self):
        self.library_root = Path("/books/library/comics")
        self.config = LANE_CONFIG["comics"]

    def _make_kavita(self, hits=None, metadata=None, folder=None):
        kavita = MagicMock()
        kavita.search_series.return_value = hits or []
        kavita.get_series_metadata.side_effect = lambda sid: (metadata or {}).get(sid, {})
        return kavita

    def _make_cv(self, name="Radiant Black", start_year=2021):
        cv = MagicMock()
        cv.get_volume.return_value = {"name": name, "start_year": start_year}
        return cv

    def test_kavita_match_within_tolerance_uses_existing_folder(self):
        decision = {"volume_id": 138352}
        kavita = self._make_kavita(
            hits=[{"series_id": 307, "name": "Radiant Black (2021)"}],
            metadata={307: {"release_year": 2022}},  # Δ1 — within tolerance
        )
        cv = self._make_cv("Radiant Black", 2021)
        with patch(
            "apply._get_kavita_folder",
            return_value="/books/library/comics/Radiant Black (2021)",
        ):
            dest = _resolve_destination(
                decision, self.library_root, kavita, cv, self.config
            )
        self.assertEqual(
            dest, Path("/books/library/comics/Radiant Black (2021)")
        )

    def test_kavita_year_mismatch_constructs_fresh(self):
        decision = {"volume_id": 138352}
        kavita = self._make_kavita(
            hits=[{"series_id": 999, "name": "Radiant Black (1999)"}],
            metadata={999: {"release_year": 1999}},  # Δ22 — too far
        )
        cv = self._make_cv("Radiant Black", 2021)
        dest = _resolve_destination(
            decision, self.library_root, kavita, cv, self.config
        )
        # Falls through to constructed path — does NOT use the stale folder.
        self.assertEqual(
            dest, Path("/books/library/comics/Radiant Black (2021)")
        )

    def test_no_kavita_match_constructs_fresh(self):
        decision = {"volume_id": 999}
        kavita = self._make_kavita(hits=[])
        cv = self._make_cv("Brand New Series", 2024)
        dest = _resolve_destination(
            decision, self.library_root, kavita, cv, self.config
        )
        self.assertEqual(
            dest, Path("/books/library/comics/Brand New Series (2024)")
        )

    def test_manga_lane_omits_year(self):
        decision = {"volume_id": 1}
        kavita = self._make_kavita(hits=[])
        cv = self._make_cv("Some Manga", 2020)
        dest = _resolve_destination(
            decision,
            Path("/books/library/manga"),
            kavita,
            cv,
            LANE_CONFIG["manga"],
        )
        self.assertEqual(dest, Path("/books/library/manga/Some Manga"))

    def test_unsafe_chars_sanitized_in_constructed_path(self):
        decision = {"volume_id": 1}
        kavita = self._make_kavita(hits=[])
        cv = self._make_cv("No/One", 2023)
        dest = _resolve_destination(
            decision, self.library_root, kavita, cv, self.config
        )
        self.assertEqual(
            dest, Path("/books/library/comics/No-One (2023)")
        )

    def test_picks_smallest_delta_among_exact_matches(self):
        """Two Kavita series same-name; pick the one with closer year."""
        decision = {"volume_id": 138352}
        kavita = self._make_kavita(
            hits=[
                {"series_id": 100, "name": "Radiant Black (2022)"},
                {"series_id": 200, "name": "Radiant Black (2025)"},
            ],
            metadata={
                100: {"release_year": 2022},  # Δ1
                200: {"release_year": 2025},  # Δ4
            },
        )
        cv = self._make_cv("Radiant Black", 2021)
        captured = {}

        def fake_get(_, sid):
            captured["sid"] = sid
            return f"/books/library/comics/sid{sid}-folder"

        with patch("apply._get_kavita_folder", side_effect=fake_get):
            _resolve_destination(
                decision, self.library_root, kavita, cv, self.config
            )
        self.assertEqual(captured["sid"], 100)  # closer year wins


class AppliedLogTests(unittest.TestCase):
    def test_append_creates_and_reads(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "src.applied.jsonl"
            decision = {
                "filename": "Foo.cbz",
                "issue_id": 100,
                "volume_id": 200,
            }
            _append_applied(log, "src", decision, Path("/books/library/comics/Foo (2022)"))

            records = _read_jsonl(log)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["filename"], "Foo.cbz")
            self.assertEqual(records[0]["issue_id"], 100)
            self.assertEqual(records[0]["source_id"], "src")
            self.assertIn("applied_at", records[0])

    def test_append_is_additive(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "src.applied.jsonl"
            for i in range(3):
                _append_applied(
                    log,
                    "src",
                    {"filename": f"f{i}.cbz", "issue_id": i, "volume_id": 0},
                    Path("/dest"),
                )
            self.assertEqual(len(_read_jsonl(log)), 3)

    def test_read_missing_returns_empty(self):
        self.assertEqual(_read_jsonl(Path("/nonexistent/path.jsonl")), [])

    def test_append_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "nested" / "dir" / "src.applied.jsonl"
            _append_applied(
                log,
                "src",
                {"filename": "a.cbz", "issue_id": 1, "volume_id": 1},
                Path("/dest"),
            )
            self.assertTrue(log.exists())


if __name__ == "__main__":
    unittest.main()
