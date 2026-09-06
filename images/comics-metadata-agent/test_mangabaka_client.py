"""Unit tests for mangabaka_client.

HTTP is mocked at the requests.Session.get level; these tests assert the
client's URL construction, pagination, caching, and response normalization.

Run: python -m unittest test_mangabaka_client -v
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from mangabaka_client import (
    MangaBakaAPIError,
    MangaBakaClient,
    MangaBakaRateLimitError,
    _pick_cover_url,
    _simplify_series,
)


def fake_response(status: int, body):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


def make_client(responses):
    """Construct a client whose Session.get returns the given queue of responses."""
    session = MagicMock()
    session.get.side_effect = list(responses)
    return MangaBakaClient(session=session), session


def page(data, has_next=False):
    return {
        "status": 200,
        "message": "ok",
        "pagination": {
            "count": 0,
            "page": 1,
            "limit": 50,
            "next": "https://example/next" if has_next else None,
            "previous": None,
        },
        "data": data,
    }


def en_title(title):
    """Minimal v2 `titles` list carrying only an English primary title."""
    return [{"language": "en", "traits": ["official"], "title": title, "is_primary": True}]


def series(id_, title="Some Series", year=2020, publisher="Some Publisher"):
    return {
        "id": id_,
        "titles": [
            {"language": "en", "traits": ["official"], "title": title, "is_primary": True},
            {"language": "ja", "traits": ["native"], "title": "ネイティブ", "is_primary": True},
            {"language": "ja-Latn", "traits": ["native"], "title": "Romanized", "is_primary": True},
        ],
        "published": {"start_date": f"{year}-08-07", "end_date": None},
        "type": "manga",
        "status": "completed",
        "content_rating": "safe",
        "publishers": [{"name": publisher}],
        "total_chapters": "10",
        "cover": {"raw": "https://cdn.mangabaka.org/cover.jpg"},
        "canonical_url": f"https://mangabaka.org/manga/{id_}/slug",
        "description": "desc",
    }


class SearchTests(unittest.TestCase):
    def test_search_single_page_returns_simplified(self):
        c, _ = make_client([
            fake_response(200, page([series(1, "Foo"), series(2, "Bar")])),
        ])
        results = c.search_series("foo")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], 1)
        self.assertEqual(results[0]["name"], "Foo")
        self.assertEqual(results[0]["start_year"], 2020)  # year → start_year
        self.assertEqual(results[0]["publisher"], "Some Publisher")
        self.assertEqual(results[0]["count_of_issues"], 10)  # from total_chapters

    def test_search_walks_pagination(self):
        c, _ = make_client([
            fake_response(200, page([series(1)], has_next=True)),
            fake_response(200, page([series(2)], has_next=True)),
            fake_response(200, page([series(3)], has_next=False)),
        ])
        results = c.search_series("x")
        self.assertEqual([r["id"] for r in results], [1, 2, 3])

    def test_search_max_pages_caps_pagination(self):
        # Even if all pages claim has_next=True, stop at max_pages.
        c, _ = make_client([
            fake_response(200, page([series(1)], has_next=True)),
            fake_response(200, page([series(2)], has_next=True)),
        ])
        results = c.search_series("x", max_pages=2)
        self.assertEqual([r["id"] for r in results], [1, 2])

    def test_search_caches_per_query(self):
        c, session = make_client([
            fake_response(200, page([series(1)])),
        ])
        c.search_series("foo")
        c.search_series("FOO")  # case-insensitive cache
        c.search_series(" foo ")  # whitespace-stripped cache
        self.assertEqual(session.get.call_count, 1)

    def test_search_url_uses_search_endpoint(self):
        c, session = make_client([
            fake_response(200, page([])),
        ])
        c.search_series("foo")
        url = session.get.call_args.args[0]
        self.assertEqual(url, "https://api.mangabaka.org/v2/series/search")
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["q"], "foo")
        self.assertEqual(params["limit"], 50)


class GetSeriesTests(unittest.TestCase):
    def test_get_series_by_id(self):
        c, session = make_client([
            fake_response(200, {"data": series(42, "Found")}),
        ])
        got = c.get_series(42)
        self.assertEqual(got["id"], 42)
        self.assertEqual(got["name"], "Found")
        url = session.get.call_args.args[0]
        self.assertEqual(url, "https://api.mangabaka.org/v2/series/42")

    def test_get_series_caches(self):
        c, session = make_client([
            fake_response(200, {"data": series(42)}),
        ])
        c.get_series(42)
        c.get_series(42)
        self.assertEqual(session.get.call_count, 1)


class ErrorHandlingTests(unittest.TestCase):
    def test_429_raises_rate_limit(self):
        c, _ = make_client([fake_response(429, {})])
        with self.assertRaises(MangaBakaRateLimitError):
            c.search_series("x")

    def test_5xx_raises_api_error(self):
        c, _ = make_client([fake_response(503, {})])
        with self.assertRaises(MangaBakaAPIError):
            c.search_series("x")

    def test_non_json_raises_api_error(self):
        r = MagicMock()
        r.status_code = 200
        r.json.side_effect = ValueError("not json")
        session = MagicMock()
        session.get.return_value = r
        c = MangaBakaClient(session=session)
        with self.assertRaises(MangaBakaAPIError):
            c.search_series("x")


class SimplifyTests(unittest.TestCase):
    def test_pick_cover_url_prefers_raw(self):
        self.assertEqual(
            _pick_cover_url({"raw": "a", "default": "b"}),
            "a",
        )

    def test_pick_cover_url_falls_to_default(self):
        self.assertEqual(_pick_cover_url({"default": "b"}), "b")

    def test_pick_cover_url_handles_nested_schema(self):
        # x250: {x1: ..., x2: ...} pattern
        self.assertEqual(
            _pick_cover_url({"x250": {"x1": "url-x1", "x2": "url-x2"}}),
            "url-x1",
        )

    def test_pick_cover_url_returns_none_when_empty(self):
        self.assertIsNone(_pick_cover_url({}))
        self.assertIsNone(_pick_cover_url(None))

    def test_simplify_series_handles_missing_publishers(self):
        s = _simplify_series({"id": 1, "titles": en_title("Foo")})
        self.assertIsNone(s["publisher"])
        self.assertIsNone(s["count_of_issues"])

    def test_simplify_handles_string_publisher_in_list(self):
        s = _simplify_series({
            "id": 1, "titles": en_title("Foo"),
            "publishers": ["Plain String Publisher"],
        })
        self.assertEqual(s["publisher"], "Plain String Publisher")

    def test_simplify_total_chapters_garbage_yields_none(self):
        s = _simplify_series({
            "id": 1, "titles": en_title("Foo"),
            "total_chapters": "ongoing",
        })
        self.assertIsNone(s["count_of_issues"])

    def test_simplify_start_year_from_published(self):
        s = _simplify_series({
            "id": 1, "titles": en_title("Foo"),
            "published": {"start_date": "2001-08-07", "end_date": "2016-08-23"},
        })
        self.assertEqual(s["start_year"], 2001)

    def test_simplify_start_year_none_when_absent_or_null(self):
        self.assertIsNone(
            _simplify_series({"id": 1, "titles": en_title("Foo")})["start_year"])
        self.assertIsNone(
            _simplify_series({
                "id": 1, "titles": en_title("Foo"),
                "published": {"start_date": None},
            })["start_year"])

    def test_simplify_splits_native_from_romanized_by_latn_subtag(self):
        s = _simplify_series({"id": 1, "titles": [
            {"language": "en", "traits": ["official"], "title": "Bleach", "is_primary": True},
            {"language": "ja", "traits": ["native"], "title": "BLEACH", "is_primary": True},
            {"language": "ja-Latn", "traits": ["native"], "title": "Burichi", "is_primary": True},
        ]})
        self.assertEqual(s["name"], "Bleach")
        self.assertEqual(s["native_title"], "BLEACH")
        self.assertEqual(s["romanized_title"], "Burichi")

    def test_simplify_title_prefers_primary_over_official(self):
        s = _simplify_series({"id": 1, "titles": [
            {"language": "en", "traits": ["official"], "title": "Official", "is_primary": False},
            {"language": "en", "traits": [], "title": "Primary", "is_primary": True},
        ]})
        self.assertEqual(s["name"], "Primary")

    def test_simplify_title_falls_back_across_languages(self):
        s = _simplify_series({"id": 1, "titles": [
            {"language": "ja", "traits": ["native"], "title": "ネイティブ", "is_primary": True},
        ]})
        self.assertEqual(s["name"], "ネイティブ")

    def test_simplify_extracts_aliases_excluding_primary(self):
        s = _simplify_series({"id": 1, "titles": [
            {"language": "en", "traits": ["official"], "title": "Foo", "is_primary": True},
            {"language": "en", "traits": [], "title": "Foo Omnibus", "is_primary": False},
            {"language": "es", "traits": [], "title": "Bar", "is_primary": False},
            {"language": "fr", "traits": [], "title": "Foo Omnibus", "is_primary": False},
            {"language": "ja", "traits": [], "title": "フー", "is_primary": False},
        ]})
        self.assertEqual(s["aliases"], ["Foo Omnibus", "Bar", "フー"])

    def test_simplify_aliases_empty_when_only_primary(self):
        s = _simplify_series({"id": 1, "titles": en_title("Foo")})
        self.assertEqual(s["aliases"], [])

    def test_simplify_site_url_uses_canonical_url(self):
        s = _simplify_series({
            "id": 1, "titles": en_title("Foo"),
            "canonical_url": "https://mangabaka.org/manga/1/foo",
        })
        self.assertEqual(s["site_url"], "https://mangabaka.org/manga/1/foo")

    def test_call_count_reflects_http_calls(self):
        c, _ = make_client([
            fake_response(200, page([series(1)])),
            fake_response(200, {"data": series(2)}),
        ])
        c.search_series("x")
        c.get_series(2)
        self.assertEqual(c.call_count, 2)


if __name__ == "__main__":
    unittest.main()
