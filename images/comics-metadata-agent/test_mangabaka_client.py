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


def series(id_, title="Some Series", year=2020, publisher="Some Publisher"):
    return {
        "id": id_,
        "title": title,
        "native_title": "ネイティブ",
        "romanized_title": "Romanized",
        "year": year,
        "type": "manga",
        "status": "completed",
        "content_rating": "safe",
        "publishers": [{"name": publisher}],
        "total_chapters": "10",
        "cover": {"raw": "https://cdn.mangabaka.dev/cover.jpg"},
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
        self.assertEqual(url, "https://api.mangabaka.dev/v1/series/search")
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
        self.assertEqual(url, "https://api.mangabaka.dev/v1/series/42")

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
        s = _simplify_series({"id": 1, "title": "Foo", "year": 2020})
        self.assertIsNone(s["publisher"])
        self.assertIsNone(s["count_of_issues"])

    def test_simplify_handles_string_publisher_in_list(self):
        s = _simplify_series({
            "id": 1, "title": "Foo", "year": 2020,
            "publishers": ["Plain String Publisher"],
        })
        self.assertEqual(s["publisher"], "Plain String Publisher")

    def test_simplify_total_chapters_garbage_yields_none(self):
        s = _simplify_series({
            "id": 1, "title": "Foo", "year": 2020,
            "total_chapters": "ongoing",
        })
        self.assertIsNone(s["count_of_issues"])

    def test_simplify_extracts_aliases_excluding_primary(self):
        s = _simplify_series({
            "id": 1,
            "title": "Foo",
            "year": 2020,
            "secondary_titles": {
                "unknown": [
                    {"title": "Foo", "note": None},      # dup of primary, drop
                    {"title": "Foo Omnibus", "note": None},
                    {"title": "Bar", "note": None},
                    {"title": "Foo Omnibus", "note": "x"},  # dup, drop
                ],
                "ja": [
                    {"title": "フー", "note": None},
                ],
            },
        })
        self.assertEqual(s["aliases"], ["Foo Omnibus", "Bar", "フー"])

    def test_simplify_aliases_empty_when_no_secondary(self):
        s = _simplify_series({"id": 1, "title": "Foo", "year": 2020})
        self.assertEqual(s["aliases"], [])

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
