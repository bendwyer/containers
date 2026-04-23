"""Unit tests for filename_parser.

Run: python -m unittest test_filename_parser -v

Cases taken from the actual Massive-Verse _unmatched/ queue.
"""

import unittest

from filename_parser import parse_filename


class ParseFilenameTests(unittest.TestCase):
    def test_simple_trailing_int(self):
        r = parse_filename("Radiant Black, 1.cbz")
        self.assertEqual(r["raw_title"], "Radiant Black")
        self.assertEqual(r["issue_guess"], "1")

    def test_uppercase_series(self):
        r = parse_filename("RADIANT BLACK, 5.cbz")
        self.assertEqual(r["raw_title"], "RADIANT BLACK")
        self.assertEqual(r["issue_guess"], "5")

    def test_slash_in_series_preserved(self):
        # NO ONE, 1.cbz came from "NO/ONE Vol 1" — slash already lost to
        # filename sanitization, agent will need to recover.
        r = parse_filename("NO ONE, 1.cbz")
        self.assertEqual(r["raw_title"], "NO ONE")
        self.assertEqual(r["issue_guess"], "1")

    def test_graphic_novel_with_embedded_vol(self):
        r = parse_filename("C.O.W.L., Vol. 1 Principles of Power 1.cbz")
        # Issue guess is the trailing "1"; series keeps the Vol qualifier.
        self.assertEqual(r["raw_title"], "C.O.W.L., Vol. 1 Principles of Power")
        self.assertEqual(r["issue_guess"], "1")

    def test_one_shot_with_year_in_title(self):
        r = parse_filename("SHIFT ONE-SHOT (2022) 1.cbz")
        self.assertEqual(r["raw_title"], "SHIFT ONE-SHOT (2022)")
        self.assertEqual(r["issue_guess"], "1")

    def test_supermassive_one_shot(self):
        r = parse_filename("Supermassive One-Shot (2023) 1.cbz")
        self.assertEqual(r["raw_title"], "Supermassive One-Shot (2023)")
        self.assertEqual(r["issue_guess"], "1")

    def test_no_trailing_int(self):
        # Edge case: no issue number at all.
        r = parse_filename("Standalone Comic.cbz")
        self.assertEqual(r["raw_title"], "Standalone Comic")
        self.assertIsNone(r["issue_guess"])

    def test_multi_digit_issue(self):
        r = parse_filename("Saga 54.cbz")
        self.assertEqual(r["raw_title"], "Saga")
        self.assertEqual(r["issue_guess"], "54")

    def test_comma_cleanup_applied_even_when_no_issue(self):
        r = parse_filename("Series With Trailing Comma,.cbz")
        self.assertEqual(r["raw_title"], "Series With Trailing Comma")
        self.assertIsNone(r["issue_guess"])

    def test_handles_non_cbz_extension(self):
        # We might get passed other files; the parser is format-agnostic.
        r = parse_filename("Some Book.pdf")
        self.assertEqual(r["raw_title"], "Some Book")


if __name__ == "__main__":
    unittest.main()
