"""Unit tests for comicvine_client.

Run: python -m unittest test_comicvine_client -v
"""

import unittest
from unittest.mock import MagicMock

from comicvine_client import (
    ComicVineClient,
    ComicVineAPIError,
    ComicVineAuthError,
    ComicVineRateLimitError,
    _year_in_range,
)


class _FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def ok(results, status_code=1):
    """Wrap results in the ComicVine envelope the client expects."""
    return {
        "error": "OK",
        "status_code": status_code,
        "results": results,
    }


VOLUME_RAW = {
    "id": 796,
    "name": "Radiant Black",
    "start_year": "2021",
    "publisher": {"id": 31, "name": "Image Comics"},
    "count_of_issues": 30,
    "description": "<p>A superhero series.</p>",
    "image": {"original_url": "https://comicvine/img/orig.jpg"},
    "site_detail_url": "https://comicvine.gamespot.com/radiant-black/4050-796/",
}

ISSUE_RAW = {
    "id": 12345,
    "issue_number": "5",
    "name": "The Showdown",
    "cover_date": "2022-05-01",
    "store_date": "2022-05-04",
    "volume": {"id": 796, "name": "Radiant Black"},
    "image": {"original_url": "https://comicvine/img/issue.jpg"},
    "site_detail_url": "https://comicvine.gamespot.com/radiant-black-5/4000-12345/",
}


class ComicVineAuthAndErrorTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.client = ComicVineClient("fake-key", session=self.session)

    def test_http_401_raises_auth_error(self):
        self.session.get.return_value = _FakeResponse(401)
        with self.assertRaises(ComicVineAuthError):
            self.client.search_volumes("Foo")

    def test_http_403_raises_auth_error(self):
        self.session.get.return_value = _FakeResponse(403)
        with self.assertRaises(ComicVineAuthError):
            self.client.search_volumes("Foo")

    def test_http_429_raises_rate_limit(self):
        self.session.get.return_value = _FakeResponse(429)
        with self.assertRaises(ComicVineRateLimitError):
            self.client.search_volumes("Foo")

    def test_http_420_raises_rate_limit(self):
        # ComicVine has historically used 420 for rate limits.
        self.session.get.return_value = _FakeResponse(420)
        with self.assertRaises(ComicVineRateLimitError):
            self.client.search_volumes("Foo")

    def test_body_status_107_raises_rate_limit(self):
        # HTTP 200 with body-level 107 = also rate limit.
        self.session.get.return_value = _FakeResponse(
            200, {"status_code": 107, "error": "Rate limit exceeded"}
        )
        with self.assertRaises(ComicVineRateLimitError):
            self.client.search_volumes("Foo")

    def test_body_status_non1_raises_api_error(self):
        self.session.get.return_value = _FakeResponse(
            200, {"status_code": 100, "error": "Object Not Found"}
        )
        with self.assertRaises(ComicVineAPIError):
            self.client.search_volumes("Foo")

    def test_http_5xx_raises_api_error(self):
        self.session.get.return_value = _FakeResponse(500)
        with self.assertRaises(ComicVineAPIError):
            self.client.search_volumes("Foo")

    def test_request_always_sends_api_key_and_json_format(self):
        self.session.get.return_value = _FakeResponse(200, ok([VOLUME_RAW]))
        self.client.search_volumes("Radiant Black")
        _, kwargs = self.session.get.call_args
        self.assertEqual(kwargs["params"]["api_key"], "fake-key")
        self.assertEqual(kwargs["params"]["format"], "json")

    def test_request_always_sends_custom_user_agent(self):
        self.session.get.return_value = _FakeResponse(200, ok([VOLUME_RAW]))
        self.client.search_volumes("Radiant Black")
        _, kwargs = self.session.get.call_args
        self.assertIn("User-Agent", kwargs["headers"])
        self.assertNotEqual(kwargs["headers"]["User-Agent"], "")


class SearchVolumesTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.client = ComicVineClient("k", session=self.session)

    def test_simplifies_volume_dto(self):
        self.session.get.return_value = _FakeResponse(200, ok([VOLUME_RAW]))
        vols = self.client.search_volumes("Radiant Black")
        self.assertEqual(len(vols), 1)
        v = vols[0]
        self.assertEqual(v["id"], 796)
        self.assertEqual(v["name"], "Radiant Black")
        self.assertEqual(v["start_year"], "2021")
        self.assertEqual(v["publisher"], "Image Comics")
        self.assertEqual(v["count_of_issues"], 30)
        self.assertEqual(v["image_url"], "https://comicvine/img/orig.jpg")

    def test_missing_publisher_field_doesnt_crash(self):
        raw = dict(VOLUME_RAW)
        raw["publisher"] = None
        self.session.get.return_value = _FakeResponse(200, ok([raw]))
        v = self.client.search_volumes("x")[0]
        self.assertIsNone(v["publisher"])

    def test_cache_shared_across_year_range_calls(self):
        # One API call, two client searches with different year ranges.
        self.session.get.return_value = _FakeResponse(200, ok([VOLUME_RAW]))
        a = self.client.search_volumes("Radiant Black")
        b = self.client.search_volumes("Radiant Black", year_range=(2000, 2025))
        c = self.client.search_volumes("Radiant Black", year_range=(2030, 2099))
        self.assertEqual(self.session.get.call_count, 1)
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertEqual(len(c), 0)  # out-of-range, filtered client-side

    def test_normalized_cache_key_same_result(self):
        self.session.get.return_value = _FakeResponse(200, ok([VOLUME_RAW]))
        self.client.search_volumes("Radiant Black")
        self.client.search_volumes("  RADIANT BLACK  ")
        self.assertEqual(self.session.get.call_count, 1)

    def test_empty_results_ok(self):
        self.session.get.return_value = _FakeResponse(200, ok([]))
        self.assertEqual(self.client.search_volumes("Nonexistent"), [])

    def test_call_count_property(self):
        self.session.get.return_value = _FakeResponse(200, ok([]))
        self.assertEqual(self.client.call_count, 0)
        self.client.search_volumes("a")
        self.client.search_volumes("b")
        self.assertEqual(self.client.call_count, 2)
        # Cache hit doesn't bump counter.
        self.client.search_volumes("a")
        self.assertEqual(self.client.call_count, 2)


class GetVolumeTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.client = ComicVineClient("k", session=self.session)

    def test_path_uses_volume_resource_prefix(self):
        self.session.get.return_value = _FakeResponse(200, ok(VOLUME_RAW))
        self.client.get_volume(796)
        args, _ = self.session.get.call_args
        self.assertIn("/volume/4050-796/", args[0])

    def test_is_cached(self):
        self.session.get.return_value = _FakeResponse(200, ok(VOLUME_RAW))
        self.client.get_volume(796)
        self.client.get_volume(796)
        self.assertEqual(self.session.get.call_count, 1)


class GetIssuesForVolumeTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.client = ComicVineClient("k", session=self.session)

    def test_filter_uses_plain_volume_id(self):
        # Issues filter takes `volume:<plain-int-id>`, no resource prefix.
        self.session.get.return_value = _FakeResponse(200, ok([ISSUE_RAW]))
        self.client.get_issues_for_volume(796)
        _, kwargs = self.session.get.call_args
        self.assertEqual(kwargs["params"]["filter"], "volume:796")

    def test_simplifies_issue_dto(self):
        self.session.get.return_value = _FakeResponse(200, ok([ISSUE_RAW]))
        issues = self.client.get_issues_for_volume(796)
        self.assertEqual(len(issues), 1)
        i = issues[0]
        self.assertEqual(i["id"], 12345)
        self.assertEqual(i["issue_number"], "5")
        self.assertEqual(i["volume_id"], 796)
        self.assertEqual(i["volume_name"], "Radiant Black")
        self.assertEqual(i["cover_date"], "2022-05-01")

    def test_is_cached_by_volume_id(self):
        self.session.get.return_value = _FakeResponse(200, ok([ISSUE_RAW]))
        self.client.get_issues_for_volume(796)
        self.client.get_issues_for_volume(796)
        self.assertEqual(self.session.get.call_count, 1)


class GetIssueTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.client = ComicVineClient("k", session=self.session)

    def test_path_uses_issue_resource_prefix(self):
        self.session.get.return_value = _FakeResponse(200, ok(ISSUE_RAW))
        self.client.get_issue(12345)
        args, _ = self.session.get.call_args
        self.assertIn("/issue/4000-12345/", args[0])

    def test_missing_volume_field_doesnt_crash(self):
        raw = dict(ISSUE_RAW)
        raw["volume"] = None
        self.session.get.return_value = _FakeResponse(200, ok(raw))
        i = self.client.get_issue(12345)
        self.assertIsNone(i["volume_id"])
        self.assertIsNone(i["volume_name"])


class YearInRangeTests(unittest.TestCase):
    def test_string_year_in_range(self):
        self.assertTrue(_year_in_range("2021", 2020, 2025))

    def test_int_year_in_range(self):
        self.assertTrue(_year_in_range(2021, 2020, 2025))

    def test_out_of_range_low(self):
        self.assertFalse(_year_in_range("2019", 2020, 2025))

    def test_out_of_range_high(self):
        self.assertFalse(_year_in_range("2026", 2020, 2025))

    def test_inclusive_boundaries(self):
        self.assertTrue(_year_in_range("2020", 2020, 2025))
        self.assertTrue(_year_in_range("2025", 2020, 2025))

    def test_none_year_excluded(self):
        self.assertFalse(_year_in_range(None, 2020, 2025))

    def test_unparseable_year_excluded(self):
        self.assertFalse(_year_in_range("abcd", 2020, 2025))
        self.assertFalse(_year_in_range("", 2020, 2025))

    def test_long_year_string_takes_first_four(self):
        # Some ComicVine data has weird trailing chars like "2021-05".
        self.assertTrue(_year_in_range("2021-05", 2020, 2025))


if __name__ == "__main__":
    unittest.main()
