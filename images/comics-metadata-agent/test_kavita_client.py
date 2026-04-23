"""Unit tests for kavita_client.

Run: python -m unittest test_kavita_client -v
"""

import unittest
from unittest.mock import MagicMock

from kavita_client import (
    KavitaClient,
    KavitaAuthError,
    KavitaAPIError,
)


class _FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


# Sample DTOs — subsets of the real Kavita responses.
SEARCH_DTO = {
    "series": [
        {
            "seriesId": 42,
            "name": "Radiant Black",
            "originalName": "Radiant Black",
            "localizedName": None,
            "libraryId": 1,
            "libraryName": "Comics",
            "format": 1,
        },
        {
            "seriesId": 43,
            "name": "Radiant Red",
            "originalName": "Radiant Red",
            "localizedName": None,
            "libraryId": 1,
            "libraryName": "Comics",
            "format": 1,
        },
    ],
    "libraries": [],
    "collections": [],
}

METADATA_DTO = {
    "seriesId": 42,
    "publishers": [{"id": 7, "name": "Image Comics"}],
    "releaseYear": 2021,
    "language": "en",
    "publicationStatus": 0,
    "webLinks": "https://comicvine.gamespot.com/...",
    # ...other fields ignored by the simplifier
    "genres": [],
    "tags": [],
}


class KavitaAuthTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.client = KavitaClient(
            "https://kavita.example",
            "fake-key",
            session=self.session,
        )

    def test_authenticate_success(self):
        self.session.post.return_value = _FakeResponse(200, {"token": "jwt-xyz"})
        self.client.authenticate()
        self.assertEqual(self.client._token, "jwt-xyz")
        # Plugin-auth endpoint hit with apiKey + pluginName as query params.
        _, kwargs = self.session.post.call_args
        self.assertEqual(kwargs["params"]["apiKey"], "fake-key")
        self.assertEqual(kwargs["params"]["pluginName"], "books-metadata-agent")

    def test_authenticate_401_raises_auth_error(self):
        self.session.post.return_value = _FakeResponse(401, {})
        with self.assertRaises(KavitaAuthError):
            self.client.authenticate()

    def test_authenticate_500_raises_auth_error(self):
        self.session.post.return_value = _FakeResponse(500, {})
        with self.assertRaises(KavitaAuthError):
            self.client.authenticate()

    def test_authenticate_missing_token_raises(self):
        self.session.post.return_value = _FakeResponse(200, {})
        with self.assertRaises(KavitaAuthError):
            self.client.authenticate()

    def test_authenticate_called_lazily_on_first_query(self):
        self.session.post.return_value = _FakeResponse(200, {"token": "t"})
        self.session.get.return_value = _FakeResponse(200, {"series": []})
        self.client.search_series("x")
        self.assertEqual(self.session.post.call_count, 1)


class KavitaSearchTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.session.post.return_value = _FakeResponse(200, {"token": "t"})
        self.client = KavitaClient(
            "https://kavita.example",
            "fake-key",
            session=self.session,
        )

    def test_search_simplifies_series_dto(self):
        self.session.get.return_value = _FakeResponse(200, SEARCH_DTO)
        result = self.client.search_series("Radiant")
        self.assertEqual(len(result), 2)
        first = result[0]
        self.assertEqual(first["series_id"], 42)
        self.assertEqual(first["name"], "Radiant Black")
        self.assertEqual(first["library_name"], "Comics")
        # Unused fields dropped.
        self.assertNotIn("originalName", first)

    def test_search_is_cached_by_normalized_query(self):
        self.session.get.return_value = _FakeResponse(200, SEARCH_DTO)
        self.client.search_series("Radiant Black")
        self.client.search_series("radiant black")  # different casing
        self.client.search_series("  Radiant Black  ")  # whitespace
        self.assertEqual(self.session.get.call_count, 1)

    def test_search_empty_result(self):
        self.session.get.return_value = _FakeResponse(200, {"series": []})
        self.assertEqual(self.client.search_series("x"), [])

    def test_search_missing_series_key_handled(self):
        # Kavita sometimes returns partial bodies; we shouldn't crash.
        self.session.get.return_value = _FakeResponse(200, {})
        self.assertEqual(self.client.search_series("x"), [])

    def test_search_500_raises_api_error(self):
        self.session.get.return_value = _FakeResponse(500, {})
        with self.assertRaises(KavitaAPIError):
            self.client.search_series("x")

    def test_search_sends_bearer_token(self):
        self.session.get.return_value = _FakeResponse(200, SEARCH_DTO)
        self.client.search_series("x")
        _, kwargs = self.session.get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer t")

    def test_search_sets_includeChapterAndFiles_false(self):
        self.session.get.return_value = _FakeResponse(200, SEARCH_DTO)
        self.client.search_series("x")
        _, kwargs = self.session.get.call_args
        self.assertEqual(kwargs["params"]["includeChapterAndFiles"], "false")


class KavitaMetadataTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.session.post.return_value = _FakeResponse(200, {"token": "t"})
        self.client = KavitaClient(
            "https://kavita.example",
            "fake-key",
            session=self.session,
        )

    def test_metadata_simplifies_dto(self):
        self.session.get.return_value = _FakeResponse(200, METADATA_DTO)
        md = self.client.get_series_metadata(42)
        self.assertEqual(md["series_id"], 42)
        self.assertEqual(md["publishers"], ["Image Comics"])
        self.assertEqual(md["release_year"], 2021)
        self.assertEqual(md["language"], "en")
        self.assertEqual(md["publication_status"], 0)
        # Unused fields not carried through.
        self.assertNotIn("genres", md)
        self.assertNotIn("tags", md)

    def test_metadata_is_cached_by_series_id(self):
        self.session.get.return_value = _FakeResponse(200, METADATA_DTO)
        self.client.get_series_metadata(42)
        self.client.get_series_metadata(42)
        self.assertEqual(self.session.get.call_count, 1)

    def test_metadata_missing_publishers_is_empty_list(self):
        self.session.get.return_value = _FakeResponse(200, {"seriesId": 42})
        md = self.client.get_series_metadata(42)
        self.assertEqual(md["publishers"], [])
        self.assertIsNone(md["release_year"])

    def test_metadata_404_raises_api_error(self):
        self.session.get.return_value = _FakeResponse(404, {})
        with self.assertRaises(KavitaAPIError):
            self.client.get_series_metadata(999)


if __name__ == "__main__":
    unittest.main()
