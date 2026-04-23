"""Kavita HTTP API client for the metadata agent.

Narrow surface: search for existing series + fetch their metadata. That's
the "does this series already live in my library, and how is it organized"
question that drives agent decisions.

See reference_kavita_api.md for protocol details (Plugin authenticate JWT,
/api/Search/search, /api/Series/metadata).
"""

from __future__ import annotations

from typing import Any

import requests


class KavitaAuthError(Exception):
    """Plugin authentication failed (bad key, wrong plugin name, server down)."""


class KavitaAPIError(Exception):
    """Non-auth API error (network, 5xx, unexpected response shape)."""


class KavitaClient:
    """Session-scoped Kavita client. Caches responses for the run.

    The agent invokes this at most a few times per item, so we cache
    aggressively in-memory. Caches are per-instance; build a fresh client
    per agent run to pick up library changes.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        plugin_name: str = "books-metadata-agent",
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._plugin_name = plugin_name
        self._session = session or requests.Session()
        self._token: str | None = None
        # Normalized-query → list of simplified series.
        self._series_cache: dict[str, list[dict[str, Any]]] = {}
        # series_id → simplified metadata.
        self._metadata_cache: dict[int, dict[str, Any]] = {}

    # ---- auth -------------------------------------------------------------

    def authenticate(self) -> None:
        """Exchange the API key for a session JWT. Idempotent."""
        url = f"{self.base_url}/api/Plugin/authenticate"
        resp = self._session.post(
            url,
            params={"apiKey": self._api_key, "pluginName": self._plugin_name},
            timeout=10,
        )
        if resp.status_code == 401:
            raise KavitaAuthError(
                f"Plugin authenticate rejected (HTTP 401). Check apiKey + pluginName."
            )
        if resp.status_code != 200:
            raise KavitaAuthError(
                f"Plugin authenticate failed: HTTP {resp.status_code}"
            )
        body = resp.json() or {}
        token = body.get("token")
        if not token:
            raise KavitaAuthError("Plugin authenticate returned no token")
        self._token = token

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            self.authenticate()
        return {"Authorization": f"Bearer {self._token}"}

    # ---- queries ----------------------------------------------------------

    def search_series(self, query: str) -> list[dict[str, Any]]:
        """Find series in the library matching a query string.

        Returns a list of simplified series dicts. Cached per-run by the
        normalized query so sibling-consistency lookups for items in the
        same run don't repeat API calls.
        """
        cache_key = query.lower().strip()
        if cache_key in self._series_cache:
            return self._series_cache[cache_key]
        url = f"{self.base_url}/api/Search/search"
        resp = self._session.get(
            url,
            params={"queryString": query, "includeChapterAndFiles": "false"},
            headers=self._auth_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            raise KavitaAPIError(
                f"Search failed for {query!r}: HTTP {resp.status_code}"
            )
        body = resp.json() or {}
        series = body.get("series") or []
        simplified = [_simplify_series(s) for s in series]
        self._series_cache[cache_key] = simplified
        return simplified

    def get_series_metadata(self, series_id: int) -> dict[str, Any]:
        """Fetch the metadata DTO for a specific series, simplified to the
        fields the agent uses for source-alignment decisions."""
        if series_id in self._metadata_cache:
            return self._metadata_cache[series_id]
        url = f"{self.base_url}/api/Series/metadata"
        resp = self._session.get(
            url,
            params={"seriesId": series_id},
            headers=self._auth_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            raise KavitaAPIError(
                f"Series metadata failed (id={series_id}): HTTP {resp.status_code}"
            )
        body = resp.json() or {}
        simplified = _simplify_metadata(body)
        self._metadata_cache[series_id] = simplified
        return simplified


def _simplify_series(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a SearchResultGroupDto.series[] entry to the fields the
    agent cares about."""
    return {
        "series_id": raw.get("seriesId"),
        "name": raw.get("name"),
        "original_name": raw.get("originalName"),
        "localized_name": raw.get("localizedName"),
        "library_id": raw.get("libraryId"),
        "library_name": raw.get("libraryName"),
        "format": raw.get("format"),
    }


def _simplify_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a SeriesMetadataDto. Keeps publishers as plain strings
    (dropping ids + lockedness fields the agent doesn't use)."""
    return {
        "series_id": raw.get("seriesId"),
        "publishers": [p.get("name") for p in (raw.get("publishers") or [])],
        "release_year": raw.get("releaseYear"),
        "language": raw.get("language"),
        "publication_status": raw.get("publicationStatus"),
        "web_links": raw.get("webLinks"),
    }
