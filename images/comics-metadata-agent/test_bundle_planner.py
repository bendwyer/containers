"""Unit tests for bundle_planner.

Covers grouping (same volume_id, base-name + publisher, year-distance gate),
canonical name/volume resolution, number assignment (CV-unique vs synthesized),
and Title-with-subtitle preservation.

Run: python -m unittest test_bundle_planner -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from bundle_planner import (
    SIBLING_YEAR_TOLERANCE,
    _apply_volume_width,
    _canonical_series_name,
    _canonical_volume_year,
    _coarse_key,
    _detect_dominant_volume_width,
    _extract_subtitle,
    _filter_mb_publishers,
    _issue_year,
    _normalize_casing,
    _resolve_title,
    _split_by_year_distance,
    _strip_series_redundancy,
    plan_bundle,
)


def make_decision(filename, volume_id, issue_id, confidence="high", decision="match"):
    return {
        "filename": filename,
        "decision": decision,
        "issue_id": issue_id,
        "volume_id": volume_id,
        "confidence": confidence,
    }


def make_volume(vid, name, start_year, publisher="Image", count=1):
    return {
        "id": vid,
        "name": name,
        "start_year": start_year,
        "publisher": publisher,
        "count_of_issues": count,
    }


def make_issue(iid, number, name=None, cover_date=None):
    return {
        "id": iid,
        "issue_number": str(number) if number is not None else None,
        "name": name,
        "cover_date": cover_date,
    }


def make_cv_client(volumes_by_id, issues_by_id):
    cv = MagicMock()
    cv.get_volume.side_effect = lambda vid: volumes_by_id.get(vid)
    cv.get_issue.side_effect = lambda iid: issues_by_id.get(iid)
    return cv


class GroupingTests(unittest.TestCase):
    def test_same_volume_id_groups_naturally(self):
        # Radiant Black pattern: 7 items, all volume 138352.
        decisions = [
            make_decision(f"RB Vol{n}.cbz", 138352, 880000 + n) for n in range(1, 8)
        ]
        volumes = {138352: make_volume(138352, "Radiant Black", 2021, count=7)}
        issues = {880000 + n: make_issue(880000 + n, n, f"Volume {n}") for n in range(1, 8)}
        cv = make_cv_client(volumes, issues)

        plans = plan_bundle(decisions, cv)
        self.assertEqual(len(plans), 7)
        # All share canonical Series + Volume
        self.assertTrue(all(p.series == "Radiant Black" for p in plans))
        self.assertTrue(all(p.volume == 2021 for p in plans))
        # Numbers from CV (unique 1..7)
        self.assertEqual(sorted(p.number for p in plans), [1, 2, 3, 4, 5, 6, 7])

    def test_per_tpb_volumes_with_subtitle_group_together(self):
        # C.O.W.L. pattern: two TPBs each in their own CV volume.
        decisions = [
            make_decision("COWL Vol 1.cbz", 77813, 468928),
            make_decision("COWL Vol 2.cbz", 84091, 498423),
        ]
        volumes = {
            77813: make_volume(77813, "C.O.W.L.: Principles of Power", 2014, count=1),
            84091: make_volume(84091, "C.O.W.L.: The Greater Good", 2015, count=1),
        }
        issues = {
            468928: make_issue(468928, 1, "Volume 1"),
            498423: make_issue(498423, 1, "Volume 2"),
        }
        cv = make_cv_client(volumes, issues)

        plans = plan_bundle(decisions, cv)
        self.assertEqual(len(plans), 2)
        self.assertTrue(all(p.series == "C.O.W.L." for p in plans))
        self.assertTrue(all(p.volume == 2014 for p in plans))  # earliest
        # CV issue numbers collide (both #1) → synthesized 1, 2
        self.assertEqual(sorted(p.number for p in plans), [1, 2])

    def test_per_year_annuals_group_together(self):
        # Supermassive pattern: same name, same publisher, different years.
        decisions = [
            make_decision("SM 2022.cbz", 141710, 908724),
            make_decision("SM 2023.cbz", 150651, 991516),
        ]
        volumes = {
            141710: make_volume(141710, "Supermassive", 2022, count=1),
            150651: make_volume(150651, "Supermassive", 2023, count=1),
        }
        issues = {
            908724: make_issue(908724, 1, name=None),  # CV had None for these
            991516: make_issue(991516, 1, name=None),
        }
        cv = make_cv_client(volumes, issues)

        plans = plan_bundle(decisions, cv)
        self.assertEqual(len(plans), 2)
        self.assertTrue(all(p.series == "Supermassive" for p in plans))
        self.assertTrue(all(p.volume == 2022 for p in plans))
        self.assertEqual(sorted(p.number for p in plans), [1, 2])

    def test_year_distance_splits_unrelated_relaunches(self):
        # Hypothetical: Batman 2011 + Batman 2016. Same publisher, same base
        # name, but 5 years apart → different relaunches, separate groups.
        decisions = [
            make_decision("Batman 2011.cbz", 1, 100),
            make_decision("Batman 2016.cbz", 2, 200),
        ]
        volumes = {
            1: make_volume(1, "Batman", 2011, publisher="DC"),
            2: make_volume(2, "Batman", 2016, publisher="DC"),
        }
        issues = {
            100: make_issue(100, 1, "Year One"),
            200: make_issue(200, 1, "Rebirth"),
        }
        cv = make_cv_client(volumes, issues)

        plans = plan_bundle(decisions, cv)
        # Two separate groups → each its own canonical Volume
        volumes_seen = {p.volume for p in plans}
        self.assertEqual(volumes_seen, {2011, 2016})

    def test_year_distance_within_tolerance_groups(self):
        # Items 1y apart with same base name should group.
        decisions = [
            make_decision("a.cbz", 1, 100),
            make_decision("b.cbz", 2, 200),
        ]
        volumes = {
            1: make_volume(1, "Foo: A", 2020, count=1),
            2: make_volume(2, "Foo: B", 2021, count=1),
        }
        issues = {
            100: make_issue(100, 1, "A"),
            200: make_issue(200, 1, "B"),
        }
        cv = make_cv_client(volumes, issues)
        plans = plan_bundle(decisions, cv)
        self.assertEqual({p.series for p in plans}, {"Foo"})
        self.assertEqual({p.volume for p in plans}, {2020})

    def test_tolerance_constant_documented(self):
        self.assertEqual(SIBLING_YEAR_TOLERANCE, 3)

    def test_different_publishers_do_not_group(self):
        # Same name, same year, different publisher → different groups.
        # E.g., Image's Supermassive vs a hypothetical Marvel Supermassive.
        decisions = [
            make_decision("a.cbz", 1, 100),
            make_decision("b.cbz", 2, 200),
        ]
        volumes = {
            1: make_volume(1, "Foo", 2022, publisher="Image"),
            2: make_volume(2, "Foo", 2022, publisher="Marvel"),
        }
        issues = {
            100: make_issue(100, 1, "A"),
            200: make_issue(200, 1, "B"),
        }
        cv = make_cv_client(volumes, issues)
        plans = plan_bundle(decisions, cv)
        # Two separate plans (groups), each in its own Series via canonical name
        # but they may share same series name. Distinguish by examining group
        # behavior — number should be 1 in each independent group.
        self.assertEqual(len(plans), 2)
        self.assertTrue(all(p.number == 1 for p in plans))


class TitleTests(unittest.TestCase):
    def test_extract_subtitle(self):
        self.assertEqual(_extract_subtitle("Foo: Bar"), "Bar")
        self.assertEqual(_extract_subtitle("Foo: Bar: Baz"), "Bar: Baz")
        self.assertIsNone(_extract_subtitle("Foo"))
        self.assertIsNone(_extract_subtitle(None))
        self.assertIsNone(_extract_subtitle(""))

    def test_subtitle_folds_into_existing_title(self):
        # CV issue.name='Volume 1', volume.name='Foo: Bar' → Title='Volume 1: Bar'
        title = _resolve_title(
            issue=make_issue(1, 1, "Volume 1"),
            volume=make_volume(1, "Foo: Bar", 2022),
            number=1,
            annual_pattern=False,
        )
        self.assertEqual(title, "Volume 1: Bar")

    def test_subtitle_already_present_in_title_left_alone(self):
        title = _resolve_title(
            issue=make_issue(1, 1, "Volume 1: Bar"),
            volume=make_volume(1, "Foo: Bar", 2022),
            number=1,
            annual_pattern=False,
        )
        self.assertEqual(title, "Volume 1: Bar")

    def test_no_subtitle_uses_issue_name_as_is(self):
        title = _resolve_title(
            issue=make_issue(1, 1, "Volume 1"),
            volume=make_volume(1, "Foo", 2022),
            number=1,
            annual_pattern=False,
        )
        self.assertEqual(title, "Volume 1")

    def test_synthesizes_when_issue_name_missing_and_subtitle_exists(self):
        title = _resolve_title(
            issue=make_issue(1, 1, name=None),
            volume=make_volume(1, "Foo: Bar", 2022),
            number=3,
            annual_pattern=False,
        )
        self.assertEqual(title, "Volume 3: Bar")

    def test_annual_pattern_falls_back_to_one_shot_year(self):
        # Supermassive case: no subtitle, issue.name=None, annual.
        title = _resolve_title(
            issue=make_issue(1, 1, name=None),
            volume=make_volume(1, "Supermassive", 2023, count=1),
            number=2,
            annual_pattern=True,
        )
        self.assertEqual(title, "One-Shot 2023")

    def test_cv_none_string_treated_as_missing(self):
        # CV sometimes returns 'None' as a string for issue.name. Handle it.
        title = _resolve_title(
            issue=make_issue(1, 1, "None"),
            volume=make_volume(1, "Supermassive", 2022),
            number=1,
            annual_pattern=True,
        )
        self.assertEqual(title, "One-Shot 2022")

    def test_empty_fallback(self):
        # No issue name, no subtitle, no annual pattern → empty string.
        title = _resolve_title(
            issue=make_issue(1, 1, name=None),
            volume=make_volume(1, "Foo", 2022),
            number=1,
            annual_pattern=False,
        )
        self.assertEqual(title, "")


class CanonicalNameTests(unittest.TestCase):
    def test_strips_subtitle(self):
        items = [
            {"cv_volume": make_volume(1, "Foo: Bar", 2020)},
            {"cv_volume": make_volume(2, "Foo: Baz", 2021)},
        ]
        self.assertEqual(_canonical_series_name(items), "Foo")

    def test_picks_most_common_base(self):
        items = [
            {"cv_volume": make_volume(1, "Foo: A", 2020)},
            {"cv_volume": make_volume(2, "Foo: B", 2021)},
            {"cv_volume": make_volume(3, "Bar", 2020)},
        ]
        self.assertEqual(_canonical_series_name(items), "Foo")

    def test_mangabaka_preserves_colon_suffix(self):
        # For MB, "Series: Subseries" denotes a distinct series, so the
        # full name must be preserved (Battle Angel Alita: Last Order is
        # not "Battle Angel Alita").
        items = [
            {
                "source": "mangabaka",
                "cv_volume": make_volume(1, "Battle Angel Alita: Last Order", 2001),
            },
        ]
        self.assertEqual(
            _canonical_series_name(items),
            "Battle Angel Alita: Last Order",
        )

    def test_comicvine_still_strips_colon_suffix(self):
        items = [
            {
                "source": "comicvine",
                "cv_volume": make_volume(1, "C.O.W.L.: Principles of Power", 2014),
            },
        ]
        self.assertEqual(_canonical_series_name(items), "C.O.W.L.")

    def test_manga_lane_preserves_colon_for_cv_source(self):
        # CV-matched manga (omnibus fallback path) — colon suffix is part
        # of edition identity, not a TPB subtitle. e.g., BAA: Last Order
        # Omnibus on CV is its own series, not a TPB of "Battle Angel Alita".
        items = [
            {
                "source": "comicvine",
                "cv_volume": make_volume(
                    1, "Battle Angel Alita: Last Order Omnibus", 2013,
                ),
            },
        ]
        self.assertEqual(
            _canonical_series_name(items, lane="manga"),
            "Battle Angel Alita: Last Order Omnibus",
        )

    def test_comics_lane_unchanged_for_cv_source(self):
        items = [
            {
                "source": "comicvine",
                "cv_volume": make_volume(1, "C.O.W.L.: Principles of Power", 2014),
            },
        ]
        self.assertEqual(
            _canonical_series_name(items, lane="comics"),
            "C.O.W.L.",
        )

    def test_canonical_volume_uses_earliest(self):
        items = [
            {"cv_volume": make_volume(1, "Foo", 2025)},
            {"cv_volume": make_volume(2, "Foo", 2022)},
            {"cv_volume": make_volume(3, "Foo", 2023)},
        ]
        self.assertEqual(_canonical_volume_year(items), 2022)

    def test_canonical_volume_handles_missing_years(self):
        items = [
            {"cv_volume": make_volume(1, "Foo", None)},
            {"cv_volume": make_volume(2, "Foo", 2024)},
        ]
        self.assertEqual(_canonical_volume_year(items), 2024)


class CoarseKeyTests(unittest.TestCase):
    def test_coarse_key_strips_subtitle_and_lowercases(self):
        item = {"cv_volume": make_volume(1, "C.O.W.L.: Principles of Power", 2014)}
        self.assertEqual(_coarse_key(item), ("comicvine", "image", "c.o.w.l."))

    def test_coarse_key_handles_no_subtitle(self):
        item = {"cv_volume": make_volume(1, "Radiant Black", 2021)}
        self.assertEqual(
            _coarse_key(item), ("comicvine", "image", "radiant black")
        )

    def test_coarse_key_separates_by_source(self):
        # Same publisher + base name but different sources don't merge.
        cv_item = {
            "cv_volume": make_volume(1, "Foo", 2020),
            "source": "comicvine",
        }
        mb_item = {
            "cv_volume": make_volume(2, "Foo", 2020),
            "source": "mangabaka",
        }
        self.assertNotEqual(_coarse_key(cv_item), _coarse_key(mb_item))


class FilterMbPublishersTests(unittest.TestCase):
    def test_single_english_with_note_kept(self):
        # Manhole shape: Kana (US) English w/ note; Square Enix Original.
        vol = {"publishers": [
            {"name": "Kana (US)", "type": "English", "note": "3 Volume; Complete"},
            {"name": "Square Enix", "type": "Original", "note": "2005, 2008, 2015"},
        ]}
        self.assertEqual(_filter_mb_publishers(vol), "Kana (US)")

    def test_drops_digital_and_unannotated_english(self):
        # BAA: Last Order shape: Omoi=digital, INKR=null, Kodansha Manga=Complete.
        vol = {"publishers": [
            {"name": "Omoi", "type": "English", "note": "digital"},
            {"name": "INKR Comics", "type": "English", "note": None},
            {"name": "Kodansha", "type": "Original", "note": ""},
            {"name": "Kodansha Manga", "type": "English",
             "note": "19 Volumes - Complete"},
            {"name": "Shueisha", "type": "Original", "note": "1972"},
        ]}
        self.assertEqual(_filter_mb_publishers(vol), "Kodansha Manga")

    def test_multi_licensee_completed_series_joined(self):
        # BAA #1 shape: two real English licensees with notes.
        vol = {"publishers": [
            {"name": "Omoi", "type": "English", "note": "digital"},
            {"name": "INKR Comics", "type": "English", "note": None},
            {"name": "Kodansha Manga", "type": "English",
             "note": "9 Volumes - Complete"},
            {"name": "VIZ Media", "type": "English",
             "note": "9 Volumes - Complete"},
        ]}
        self.assertEqual(
            _filter_mb_publishers(vol),
            "Kodansha Manga, VIZ Media",
        )

    def test_returns_none_when_nothing_survives(self):
        vol = {"publishers": [
            {"name": "Foo Digital", "type": "English", "note": "digital"},
            {"name": "Bar Reader", "type": "English", "note": None},
        ]}
        self.assertIsNone(_filter_mb_publishers(vol))

    def test_returns_none_when_publishers_missing(self):
        self.assertIsNone(_filter_mb_publishers({}))
        self.assertIsNone(_filter_mb_publishers({"publishers": None}))

    def test_drops_digital_substring_and_chapters_only_notes(self):
        # Shangri-La Frontier shape: notes carry "Digital" as substring
        # (not as exact value), and K Manga tracks chapters not volumes.
        # Only Kodansha Manga's note mentions "Volumes".
        vol = {"publishers": [
            {"name": "Omoi", "type": "English", "note": "Digital - Cancelled"},
            {"name": "INKR Comics", "type": "English",
             "note": "Digital - Cancelled"},
            {"name": "K Manga", "type": "English",
             "note": "260 Chapters - Ongoing; Digital"},
            {"name": "Kodansha", "type": "Original", "note": ""},
            {"name": "Kodansha Manga", "type": "English",
             "note": "24 Volumes - Ongoing; digital"},
        ]}
        self.assertEqual(_filter_mb_publishers(vol), "Kodansha Manga")

    def test_keeps_singular_volume_in_note(self):
        # Manhole-style: "3 Volume; Complete" — singular form must match.
        vol = {"publishers": [
            {"name": "Kana (US)", "type": "English",
             "note": "3 Volume; Complete"},
        ]}
        self.assertEqual(_filter_mb_publishers(vol), "Kana (US)")

    def test_keeps_abbreviated_vols_in_note(self):
        # BAA: Mars Chronicle shape: Kodansha Manga's note uses "Vols"
        # abbreviation rather than "Volumes". Filter must catch both.
        vol = {"publishers": [
            {"name": "Omoi", "type": "English", "note": "digital"},
            {"name": "INKR Comics", "type": "English", "note": None},
            {"name": "Kodansha", "type": "Original", "note": ""},
            {"name": "Kodansha Manga", "type": "English",
             "note": "11 Vols - Complete"},
        ]}
        self.assertEqual(_filter_mb_publishers(vol), "Kodansha Manga")


class IssueYearTests(unittest.TestCase):
    def test_extracts_from_cover_date(self):
        self.assertEqual(_issue_year({"cover_date": "2022-08-15"}), "2022")

    def test_falls_back_to_store_date(self):
        self.assertEqual(
            _issue_year({"cover_date": None, "store_date": "2023-01-01"}),
            "2023",
        )

    def test_returns_none_when_both_missing(self):
        self.assertIsNone(_issue_year({}))


class SplitByYearDistanceTests(unittest.TestCase):
    def test_single_item_returned_as_one_cluster(self):
        items = [{"cv_volume": make_volume(1, "Foo", 2020)}]
        clusters = _split_by_year_distance(items)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 1)

    def test_within_tolerance_single_cluster(self):
        items = [
            {"cv_volume": make_volume(1, "Foo", 2020)},
            {"cv_volume": make_volume(2, "Foo", 2022)},
            {"cv_volume": make_volume(3, "Foo", 2023)},
        ]
        clusters = _split_by_year_distance(items)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 3)

    def test_beyond_tolerance_splits(self):
        items = [
            {"cv_volume": make_volume(1, "Foo", 2011)},
            {"cv_volume": make_volume(2, "Foo", 2016)},  # 5y gap
        ]
        clusters = _split_by_year_distance(items)
        self.assertEqual(len(clusters), 2)


class EndToEndPlanTests(unittest.TestCase):
    """Integration-flavored — feed a Massive-Verse-style mixed bundle and
    check the plan is what we'd expect post-heal."""

    def test_massive_verse_smoke(self):
        decisions = [
            make_decision("Radiant Black, 1.cbz", 138352, 881056),
            make_decision("RADIANT BLACK, 5.cbz", 138352, 1107997),
            make_decision("C.O.W.L., Vol. 1.cbz", 77813, 468928),
            make_decision("C.O.W.L., Vol. 2.cbz", 84091, 498423),
            make_decision("Supermassive 2022.cbz", 141710, 908724),
            make_decision("Supermassive 2023.cbz", 150651, 991516),
            make_decision("Inferno Girl Red, 1.cbz", 157023, 1046201),
        ]
        volumes = {
            138352: make_volume(138352, "Radiant Black", 2021, count=7),
            77813: make_volume(77813, "C.O.W.L.: Principles of Power", 2014, count=1),
            84091: make_volume(84091, "C.O.W.L.: The Greater Good", 2015, count=1),
            141710: make_volume(141710, "Supermassive", 2022, count=1),
            150651: make_volume(150651, "Supermassive", 2023, count=1),
            157023: make_volume(157023, "Inferno Girl Red", 2023, count=1),
        }
        issues = {
            881056: make_issue(881056, 1, "Volume 1: (Not So) Secret Origin", "2022-04-01"),
            1107997: make_issue(1107997, 5, "Volume 5: Crisis", "2024-08-01"),
            468928: make_issue(468928, 1, "Volume 1", "2014-12-01"),
            498423: make_issue(498423, 1, "Volume 2", "2015-09-01"),
            908724: make_issue(908724, 1, name=None, cover_date="2022-09-01"),
            991516: make_issue(991516, 1, name=None, cover_date="2023-01-01"),
            1046201: make_issue(1046201, 1, "Book One", "2023-11-01"),
        }
        cv = make_cv_client(volumes, issues)

        plans = plan_bundle(decisions, cv)
        self.assertEqual(len(plans), 7)

        # Index by filename for easy assertions
        by_fn = {p.filename: p for p in plans}

        # Radiant Black: same canonical series+volume, CV-derived numbers
        rb1 = by_fn["Radiant Black, 1.cbz"]
        rb5 = by_fn["RADIANT BLACK, 5.cbz"]
        self.assertEqual(rb1.series, "Radiant Black")
        self.assertEqual(rb5.series, "Radiant Black")
        self.assertEqual(rb1.volume, 2021)
        self.assertEqual(rb5.volume, 2021)
        self.assertEqual(rb1.number, 1)
        self.assertEqual(rb5.number, 5)

        # C.O.W.L.: collapsed to base series, sequential numbers, subtitle preserved
        cowl1 = by_fn["C.O.W.L., Vol. 1.cbz"]
        cowl2 = by_fn["C.O.W.L., Vol. 2.cbz"]
        self.assertEqual(cowl1.series, "C.O.W.L.")
        self.assertEqual(cowl2.series, "C.O.W.L.")
        self.assertEqual(cowl1.volume, 2014)
        self.assertEqual(cowl2.volume, 2014)
        self.assertEqual({cowl1.number, cowl2.number}, {1, 2})
        self.assertEqual(cowl1.title, "Volume 1: Principles of Power")
        self.assertEqual(cowl2.title, "Volume 2: The Greater Good")

        # Supermassive: synthesized One-Shot Title, sequential numbers
        sm22 = by_fn["Supermassive 2022.cbz"]
        sm23 = by_fn["Supermassive 2023.cbz"]
        self.assertEqual(sm22.series, "Supermassive")
        self.assertEqual(sm22.volume, 2022)
        self.assertEqual({sm22.number, sm23.number}, {1, 2})
        self.assertEqual(sm22.title, "One-Shot 2022")
        self.assertEqual(sm23.title, "One-Shot 2023")

        # Inferno Girl Red: lone group, CV title used as-is
        igr = by_fn["Inferno Girl Red, 1.cbz"]
        self.assertEqual(igr.series, "Inferno Girl Red")
        self.assertEqual(igr.volume, 2023)
        self.assertEqual(igr.title, "Book One")


class VolumeWidthNormalizationTests(unittest.TestCase):
    def test_dominant_width_single_digit(self):
        titles = ["Volume 1", "Volume 2", "Volume 3", "Volume 4", "Volume 007"]
        self.assertEqual(_detect_dominant_volume_width(titles), 1)

    def test_dominant_width_two_digit(self):
        titles = ["Volume 01: A", "Volume 02: B", "Volume 5: C"]
        self.assertEqual(_detect_dominant_volume_width(titles), 2)

    def test_no_volume_titles_returns_none(self):
        titles = ["Book One", "TPB", "One-Shot 2022"]
        self.assertIsNone(_detect_dominant_volume_width(titles))

    def test_apply_pads_short_to_width(self):
        self.assertEqual(_apply_volume_width("Volume 7", 2), "Volume 07")

    def test_apply_strips_long_to_width(self):
        self.assertEqual(_apply_volume_width("Volume 007: Foo", 1), "Volume 7: Foo")

    def test_apply_preserves_subtitle(self):
        self.assertEqual(
            _apply_volume_width("Volume 7: All-New All-Different", 1),
            "Volume 7: All-New All-Different",
        )

    def test_apply_handles_case_insensitive(self):
        self.assertEqual(_apply_volume_width("VOLUME 007", 1), "Volume 7")
        self.assertEqual(_apply_volume_width("volume 7", 2), "Volume 07")

    def test_apply_no_match_returns_unchanged(self):
        self.assertEqual(_apply_volume_width("Book One", 1), "Book One")
        self.assertEqual(_apply_volume_width("", 1), "")

    def test_radiant_black_outlier_normalized_in_full_plan(self):
        """End-to-end: a series with 6×width-1 + 1×width-3 should normalize
        the outlier to width-1, matching what the user wants for RB Vol 7."""
        decisions = [
            make_decision(f"RB Vol{n}.cbz", 138352, 880000 + n) for n in range(1, 8)
        ]
        volumes = {138352: make_volume(138352, "Radiant Black", 2021, count=7)}
        # 1-6 are "Volume N" (width 1); 7 is "Volume 007" (width 3) — CV's quirk.
        issues = {
            **{
                880000 + n: make_issue(880000 + n, n, f"Volume {n}: Story {n}")
                for n in range(1, 7)
            },
            880007: make_issue(880007, 7, "Volume 007: All-New All-Different"),
        }
        cv = make_cv_client(volumes, issues)
        plans = plan_bundle(decisions, cv)
        by_n = {p.number: p for p in plans}
        self.assertEqual(by_n[1].title, "Volume 1: Story 1")
        self.assertEqual(by_n[6].title, "Volume 6: Story 6")
        # The outlier — pulled in line.
        self.assertEqual(by_n[7].title, "Volume 7: All-New All-Different")


class SeriesRedundancyTests(unittest.TestCase):
    def test_exact_match_drops_title(self):
        self.assertEqual(_strip_series_redundancy("Foo", "Foo"), "")

    def test_case_insensitive_exact_match(self):
        self.assertEqual(_strip_series_redundancy("FOO", "foo"), "")

    def test_strips_series_colon_prefix(self):
        self.assertEqual(
            _strip_series_redundancy("Foo: Bar", "Foo"),
            "Bar",
        )

    def test_strips_case_insensitively(self):
        self.assertEqual(
            _strip_series_redundancy("FOO: Bar", "foo"),
            "Bar",
        )

    def test_does_not_strip_bare_prefix(self):
        # 'Foo returns home' starts with 'Foo' but isn't 'Foo:' — leave alone.
        self.assertEqual(
            _strip_series_redundancy("Foo returns home", "Foo"),
            "Foo returns home",
        )

    def test_unrelated_title_passes_through(self):
        self.assertEqual(
            _strip_series_redundancy("Volume 1: Subtitle", "Foo"),
            "Volume 1: Subtitle",
        )

    def test_empty_inputs_pass_through(self):
        self.assertEqual(_strip_series_redundancy("", "Foo"), "")
        self.assertEqual(_strip_series_redundancy("Bar", ""), "Bar")


class CasingNormalizationTests(unittest.TestCase):
    def test_uppercase_long_string_title_cased(self):
        self.assertEqual(
            _normalize_casing("ALL-NEW ALL-DIFFERENT"),
            "All-New All-Different",
        )

    def test_short_uppercase_left_alone(self):
        # Likely an acronym like 'TPB' or 'OGN'.
        self.assertEqual(_normalize_casing("TPB"), "TPB")
        self.assertEqual(_normalize_casing("OGN"), "OGN")
        self.assertEqual(_normalize_casing("FBI"), "FBI")

    def test_mixed_case_left_alone(self):
        self.assertEqual(_normalize_casing("Volume 1: Subtitle"), "Volume 1: Subtitle")

    def test_pure_digits_or_punctuation_pass_through(self):
        self.assertEqual(_normalize_casing("12345"), "12345")
        self.assertEqual(_normalize_casing("---"), "---")

    def test_empty_string(self):
        self.assertEqual(_normalize_casing(""), "")


class IgnoresUncertainTests(unittest.TestCase):
    def test_uncertain_decisions_dropped(self):
        decisions = [
            make_decision("matched.cbz", 1, 100),
            {"filename": "uncertain.cbz", "decision": "uncertain", "reasoning": "..."},
        ]
        volumes = {1: make_volume(1, "Foo", 2022)}
        issues = {100: make_issue(100, 1, "Bar")}
        cv = make_cv_client(volumes, issues)
        plans = plan_bundle(decisions, cv)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].filename, "matched.cbz")


if __name__ == "__main__":
    unittest.main()
