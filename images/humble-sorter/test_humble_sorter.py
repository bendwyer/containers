"""Unit tests for humble_sorter.

Run with: python -m pytest test_humble_sorter.py -v
Or:       python -m unittest test_humble_sorter

Fixture data mirrors real bundles from the Humble backlog so regressions
on known cases are caught immediately.
"""

import unittest
from pathlib import Path
import tempfile

from humble_sorter import (
    classify_bundle,
    classify_item,
    pick_preferred_file,
    write_manifests,
    EBOOK_SIZE_THRESHOLD_MIB,
)


# Helpers ----------------------------------------------------------------

MIB = 1024 * 1024


def mib(n):
    """Shorthand: n * 1024 * 1024."""
    return int(n * MIB)


def make_item(bundle_dir, item_name, files):
    """Create an item directory with the given (filename, size_mib) files."""
    item_dir = bundle_dir / item_name
    item_dir.mkdir(parents=True, exist_ok=True)
    for filename, size_mib in files:
        (item_dir / filename).write_bytes(b"\0" * mib(size_mib))
    return item_dir


# classify_bundle --------------------------------------------------------

class ClassifyBundleTests(unittest.TestCase):
    def test_image_comics_bundle_is_comics(self):
        self.assertEqual(
            classify_bundle("Humble Comics Bundle: Massive-Verse by Image Comics"),
            "comics",
        )

    def test_dark_horse_bundle_is_comics(self):
        self.assertEqual(
            classify_bundle(
                "Humble Comics Bundle: Best of Humble Bundle: The Witcher + Cyberpunk by Dark Horse"
            ),
            "comics",
        )

    def test_explicit_manga_keyword_in_title_is_manga(self):
        self.assertEqual(
            classify_bundle("Humble Manga Bundle: Kana Manga Mini"),
            "manga",
        )

    def test_kodansha_publisher_in_title_is_manga(self):
        self.assertEqual(
            classify_bundle("Humble Bundle: Lone Wolf & Cub ENCORE by Kodansha"),
            "manga",
        )

    def test_viz_publisher_in_title_is_manga(self):
        self.assertEqual(
            classify_bundle("Humble Manga Bundle by VIZ"),
            "manga",
        )

    def test_ebook_bundle_prefix_is_ebook(self):
        self.assertEqual(
            classify_bundle("Humble Book Bundle: Forgotten Realms Vault by Wizards of the Coast"),
            "ebook",
        )

    def test_manhwa_keyword_is_manga(self):
        self.assertEqual(classify_bundle("Humble Manhwa Bundle"), "manga")

    def test_manhua_keyword_is_manga(self):
        self.assertEqual(classify_bundle("Humble Manhua Bundle"), "manga")

    def test_unknown_publisher_defaults_comics(self):
        self.assertEqual(
            classify_bundle("Humble Bundle: Something Random"),
            "comics",
        )

    def test_manga_keyword_case_insensitive(self):
        self.assertEqual(classify_bundle("humble MANGA bundle"), "manga")

    def test_manga_keyword_embedded_not_triggered(self):
        # MANGA_TITLE_RE uses \b boundaries so embedded substrings don't match.
        self.assertEqual(classify_bundle("Humble Comics Bundle: Pomegranate"), "comics")


# classify_item ----------------------------------------------------------

class ClassifyItemTests(unittest.TestCase):
    def test_manga_marker_in_filename_overrides_comics_bundle(self):
        self.assertEqual(
            classify_item("The Witcher Ronin (Manga).pdf", mib(156), "comics"),
            "manga",
        )

    def test_manga_marker_suffix_variant(self):
        self.assertEqual(
            classify_item("Some Title: Manga.pdf", mib(100), "comics"),
            "manga",
        )

    def test_manhwa_marker_in_filename(self):
        self.assertEqual(
            classify_item("Tower of God (Manhwa).pdf", mib(200), "comics"),
            "manga",
        )

    def test_large_comic_in_comics_bundle_is_comics(self):
        self.assertEqual(
            classify_item(
                "Andrzej Sapkowski's The Witcher: A Grain of Truth.pdf",
                mib(275),
                "comics",
            ),
            "comics",
        )

    def test_manga_bundle_lane_passes_through(self):
        self.assertEqual(
            classify_item("Lone Wolf and Cub Volume 1.pdf", mib(500), "manga"),
            "manga",
        )

    def test_ebook_bundle_small_pdf_is_ebook(self):
        self.assertEqual(
            classify_item("Forgotten Realms Vault: Dragonlance Volume 1.pdf", mib(5), "ebook"),
            "ebook",
        )

    def test_ebook_bundle_size_veto_reclassifies_large_pdf_to_comics(self):
        self.assertEqual(
            classify_item("Secret Comic in Book Bundle.pdf", mib(200), "ebook"),
            "comics",
        )

    def test_ebook_bundle_boundary_size(self):
        self.assertEqual(
            classify_item("Novel.pdf", mib(EBOOK_SIZE_THRESHOLD_MIB) - 1, "ebook"),
            "ebook",
        )
        self.assertEqual(
            classify_item("Novel.pdf", mib(EBOOK_SIZE_THRESHOLD_MIB), "ebook"),
            "comics",
        )

    def test_default_lane_is_comics(self):
        self.assertEqual(
            classify_item("Some Issue.pdf", mib(100), "comics"),
            "comics",
        )


# pick_preferred_file ----------------------------------------------------

class PickPreferredFileTests(unittest.TestCase):
    """Size-based selection among format variants of the same item."""

    def _prepare(self, files):
        """files: list of (filename, size_mib). Returns list of Paths."""
        td = tempfile.mkdtemp()
        paths = []
        for filename, size_mib in files:
            p = Path(td) / filename
            p.write_bytes(b"\0" * mib(size_mib))
            paths.append(p)
        self.addCleanup(lambda: [p.unlink() for p in paths])
        return paths

    def test_largest_pdf_wins_over_cbz_and_epub(self):
        # User's common case: PDF largest, wins.
        paths = self._prepare([("x.pdf", 500), ("x.cbz", 200), ("x.epub", 2)])
        chosen = pick_preferred_file(paths)
        self.assertEqual(chosen.suffix, ".pdf")

    def test_largest_cbz_wins_when_it_is_actually_larger(self):
        # Counter-case: CBZ happens to be the largest (high-res scans).
        paths = self._prepare([("x.pdf", 200), ("x.cbz", 800)])
        chosen = pick_preferred_file(paths)
        self.assertEqual(chosen.suffix, ".cbz")

    def test_hq_pdf_wins_over_non_hq_by_size(self):
        # HQ variants are naturally larger, so size-based selection
        # handles them without a special rule.
        paths = self._prepare([("x.pdf", 50), ("x (hq).pdf", 800)])
        chosen = pick_preferred_file(paths)
        self.assertEqual(chosen.name, "x (hq).pdf")

    def test_single_file_is_chosen(self):
        paths = self._prepare([("only.pdf", 100)])
        chosen = pick_preferred_file(paths)
        self.assertEqual(chosen.name, "only.pdf")

    def test_ignores_tie_deterministically(self):
        # Equal sizes — any consistent winner is fine; just don't crash.
        paths = self._prepare([("a.pdf", 100), ("a.cbz", 100)])
        chosen = pick_preferred_file(paths)
        self.assertIn(chosen.suffix, (".pdf", ".cbz"))


# End-to-end classification -------------------------------------------------

class BundleIntegrationTests(unittest.TestCase):
    """Parametric tests that mirror actual humble-cli details outputs."""

    def _run_bundle_simple(self, title, items):
        """Single-format items: list of (item_name, size_mib).
        Returns the buckets dict from write_manifests.
        """
        with tempfile.TemporaryDirectory() as td:
            bundle_dir = Path(td) / "bundle"
            bundle_dir.mkdir()
            for item_name, size_mib in items:
                make_item(bundle_dir, item_name, [(f"{item_name}.pdf", size_mib)])
            output_dir = Path(td) / "classified"
            return write_manifests(bundle_dir, title, output_dir)

    def _run_bundle_multi_format(self, title, items):
        """Multi-format items: list of (item_name, [(filename, size_mib), ...]).
        Returns (buckets, manifest_contents_per_lane).
        """
        with tempfile.TemporaryDirectory() as td:
            bundle_dir = Path(td) / "bundle"
            bundle_dir.mkdir()
            for item_name, files in items:
                make_item(bundle_dir, item_name, files)
            output_dir = Path(td) / "classified"
            buckets = write_manifests(bundle_dir, title, output_dir)
            contents = {
                lane: (output_dir / f"{lane}.txt").read_text()
                for lane in ("comics", "manga", "ebook")
            }
            return buckets, contents

    def test_massive_verse_all_comics(self):
        buckets = self._run_bundle_simple(
            "Humble Comics Bundle: Massive-Verse by Image Comics",
            [
                ("Radiant Black Vol. 1", 411),
                ("Rogue Sun Vol 4", 552),
                ("NO ONE Vol 1", 563),
                ("Inferno Girl Red Vol. 1", 322),
                ("DEAD LUCKY Vol 2", 116),
            ],
        )
        self.assertEqual(len(buckets["comics"]), 5)
        self.assertEqual(len(buckets["manga"]), 0)
        self.assertEqual(len(buckets["ebook"]), 0)
        # Format hint present on every manifest entry.
        for _, fmt in buckets["comics"]:
            self.assertEqual(fmt, "pdf")

    def test_witcher_cyberpunk_mixed_comics_and_manga(self):
        buckets = self._run_bundle_simple(
            "Humble Comics Bundle: Best of Humble Bundle: The Witcher + Cyberpunk by Dark Horse",
            [
                ("The Witcher Volume 1 House of Glass", 212),
                ("The Witcher Ronin (Manga)", 156),
                ("Cyberpunk 2077 Blackout", 1100),
                ("Andrzej Sapkowski's The Witcher A Grain of Truth", 275),
                ("GWENT Art of The Witcher Card Game Volume 2", 1700),
            ],
        )
        self.assertEqual(len(buckets["comics"]), 4)
        self.assertEqual(len(buckets["manga"]), 1)
        self.assertEqual(len(buckets["ebook"]), 0)
        manga_paths = [p for p, _ in buckets["manga"]]
        self.assertIn(
            "The Witcher Ronin (Manga)/The Witcher Ronin (Manga).pdf",
            manga_paths,
        )

    def test_forgotten_realms_vault_all_ebook(self):
        buckets = self._run_bundle_simple(
            "Humble Book Bundle: Forgotten Realms Vault by Wizards of the Coast",
            [
                ("Dragonlance Chronicles Volume 1", 4),
                ("Dragonlance Chronicles Volume 2", 5),
                ("Drizzt Do'Urden Book 1", 3),
            ],
        )
        self.assertEqual(len(buckets["ebook"]), 3)
        self.assertEqual(len(buckets["comics"]), 0)

    def test_kana_manga_mini_all_manga(self):
        buckets = self._run_bundle_simple(
            "Humble Manga Bundle: Kana Manga Mini",
            [("Manga Title 1", 150), ("Manga Title 2", 180)],
        )
        self.assertEqual(len(buckets["manga"]), 2)

    def test_manifest_files_always_written(self):
        with tempfile.TemporaryDirectory() as td:
            bundle_dir = Path(td) / "b"
            bundle_dir.mkdir()
            make_item(bundle_dir, "item", [("One Comic.pdf", 300)])
            output_dir = Path(td) / "out"
            write_manifests(bundle_dir, "Humble Comics Bundle: ...", output_dir)
            self.assertTrue((output_dir / "comics.txt").exists())
            self.assertTrue((output_dir / "manga.txt").exists())
            self.assertTrue((output_dir / "ebook.txt").exists())
            self.assertEqual((output_dir / "manga.txt").read_text(), "")
            self.assertEqual((output_dir / "ebook.txt").read_text(), "")

    # Multi-format item tests ------------------------------------------

    def test_multi_format_item_picks_largest(self):
        # Item has CBZ + PDF + EPUB. User's rule: pick largest.
        buckets, contents = self._run_bundle_multi_format(
            "Humble Comics Bundle: Dynamite Mixed",
            [
                (
                    "Some Comic",
                    [("Some Comic.cbz", 200), ("Some Comic.pdf", 1500), ("Some Comic.epub", 2)],
                ),
            ],
        )
        self.assertEqual(len(buckets["comics"]), 1)
        rel, fmt = buckets["comics"][0]
        self.assertEqual(fmt, "pdf")
        self.assertTrue(rel.endswith(".pdf"))
        # Manifest is tab-separated: path\tformat.
        self.assertIn("\tpdf", contents["comics"])

    def test_multi_format_item_cbz_wins_when_largest(self):
        buckets, _ = self._run_bundle_multi_format(
            "Humble Comics Bundle: Dynamite Mixed",
            [
                (
                    "Better Scanned Comic",
                    [("x.pdf", 200), ("x.cbz", 800)],
                ),
            ],
        )
        _, fmt = buckets["comics"][0]
        self.assertEqual(fmt, "cbz")

    def test_multi_format_respects_manga_marker(self):
        # Even with multiple formats, item-level manga marker routes correctly.
        buckets, _ = self._run_bundle_multi_format(
            "Humble Comics Bundle: ... by Dark Horse",
            [
                (
                    "Something Ronin (Manga)",
                    [("x.pdf", 500), ("x.cbz", 200)],
                ),
            ],
        )
        self.assertEqual(len(buckets["manga"]), 1)
        self.assertEqual(len(buckets["comics"]), 0)

    def test_mixed_bundle_each_item_picked_independently(self):
        buckets, _ = self._run_bundle_multi_format(
            "Humble Comics Bundle: Bigger Mixed",
            [
                ("Item A", [("a.pdf", 300), ("a.cbz", 100)]),
                ("Item B", [("b.pdf", 100), ("b.cbz", 800)]),
                ("Item C", [("c.pdf", 200)]),
            ],
        )
        formats = sorted(fmt for _, fmt in buckets["comics"])
        self.assertEqual(formats, ["cbz", "pdf", "pdf"])

    def test_multi_format_with_hq_variant(self):
        # Within PDF family, HQ is larger → wins.
        buckets, _ = self._run_bundle_multi_format(
            "Humble Comics Bundle: HQ Test",
            [
                (
                    "Some Issue",
                    [("x.pdf", 50), ("x (hq).pdf", 800), ("x.cbz", 150)],
                ),
            ],
        )
        rel, fmt = buckets["comics"][0]
        self.assertEqual(fmt, "pdf")
        self.assertIn("(hq)", rel)


if __name__ == "__main__":
    unittest.main()
