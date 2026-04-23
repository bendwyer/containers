"""Unit tests for series_grouper.

Run: python -m unittest test_series_grouper -v

Uses the real Massive-Verse _unmatched/ set as fixture data — this is
the first bundle the agent is designed against, and clustering these
correctly is a load-bearing property.
"""

import unittest
from pathlib import Path

from series_grouper import canonical_series_key, group_by_series


MASSIVE_VERSE_UNMATCHED = [
    "C.O.W.L., Vol. 1 Principles of Power 1.cbz",
    "C.O.W.L., Vol. 2 The Greater Good 1.cbz",
    "Inferno Girl Red, 1.cbz",
    "NO ONE, 1.cbz",
    "Radiant Black, 1.cbz",
    "Radiant Black, 2.cbz",
    "Radiant Black, 4.cbz",
    "RADIANT BLACK, 5.cbz",
    "Radiant Pink, 1.cbz",
    "Rogue Sun, 1.cbz",
    "Rogue Sun, 2.cbz",
    "ROGUE SUN, 3.cbz",
    "SHIFT ONE-SHOT (2022) 1.cbz",
    "Supermassive One-Shot (2022) 1.cbz",
    "Supermassive One-Shot (2023) 1.cbz",
    "The Dead Lucky, 1.cbz",
]


class CanonicalKeyTests(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(canonical_series_key("RADIANT BLACK"), "radiant black")

    def test_strips_punctuation(self):
        self.assertEqual(
            canonical_series_key("C.O.W.L., Vol. 1 Principles of Power"),
            "cowl vol 1 principles of power",
        )

    def test_preserves_hyphens(self):
        # Hyphens are meaningful in series like "Love and Rockets: Comic-Off".
        self.assertEqual(canonical_series_key("One-Shot"), "one-shot")

    def test_collapses_multi_spaces(self):
        self.assertEqual(canonical_series_key("A    B"), "a b")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(canonical_series_key("  Foo Bar  "), "foo bar")


class GroupByTests(unittest.TestCase):
    def test_massive_verse_grouping(self):
        paths = [Path(f"/u/{name}") for name in MASSIVE_VERSE_UNMATCHED]
        groups = group_by_series(paths)

        # All three Radiant Black variants (casing) collapse to one group.
        rb = groups["radiant black"]
        self.assertEqual(len(rb), 4)
        # Sorted within a group so #1 is processed before #2, etc.
        self.assertEqual(
            [p.name for p in rb],
            [
                "RADIANT BLACK, 5.cbz",
                "Radiant Black, 1.cbz",
                "Radiant Black, 2.cbz",
                "Radiant Black, 4.cbz",
            ],
        )

        # Three Rogue Sun variants collapse.
        rs = groups["rogue sun"]
        self.assertEqual(len(rs), 3)

        # The two C.O.W.L. volumes are DIFFERENT series groups — different
        # graphic-novel volumes, each with its own ComicVine volume id.
        self.assertIn("cowl vol 1 principles of power", groups)
        self.assertIn("cowl vol 2 the greater good", groups)
        self.assertEqual(len(groups["cowl vol 1 principles of power"]), 1)
        self.assertEqual(len(groups["cowl vol 2 the greater good"]), 1)

        # The two Supermassive one-shots differ only by year — they're
        # separate ComicVine issues, but same canonical key: that's fine,
        # the agent will disambiguate within the group using cover year.
        sm = groups["supermassive one-shot 2022"]
        self.assertEqual(len(sm), 1)
        self.assertIn("supermassive one-shot 2023", groups)

    def test_empty_input(self):
        self.assertEqual(group_by_series([]), {})

    def test_singletons(self):
        paths = [Path("/u/NO ONE, 1.cbz"), Path("/u/Inferno Girl Red, 1.cbz")]
        groups = group_by_series(paths)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups["no one"]), 1)
        self.assertEqual(len(groups["inferno girl red"]), 1)


if __name__ == "__main__":
    unittest.main()
