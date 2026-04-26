"""ComicVine v1 API client for the metadata agent.

Narrow surface focused on what the agent needs:
  - Search for candidate volumes by series name.
  - Fetch issues for a volume (to find the right issue number).
  - Fetch a single volume or issue for more detail (cover URL, description).

Rate-limit discipline: ComicVine limits to 200 requests per resource per hour.
All client responses are cached in-memory for the run. Tests verify cache hits.

ComicVine quirks that bleed through:
  - URLs require resource-type prefixes: /volume/4050-<id>/, /issue/4000-<id>/.
    Callers pass plain integer ids; the client prepends prefixes.
  - Every response carries status_code=1 on success. 107 means rate-limit; any
    other value is an application-level error even if HTTP was 200.
  - A custom User-Agent is required — default requests UA gets 403s.
"""

from __future__ import annotations

from typing import Any

import requests


class ComicVineAuthError(Exception):
    """API key rejected or missing."""


class ComicVineRateLimitError(Exception):
    """Rate limit exceeded. Caller should back off or surface to user."""


class ComicVineAPIError(Exception):
    """Non-auth, non-rate-limit error (network, bad response, 5xx)."""


class ComicVineClient:
    BASE_URL = "https://comicvine.gamespot.com/api"
    VOLUME_RESOURCE_PREFIX = "4050-"
    ISSUE_RESOURCE_PREFIX = "4000-"

    def __init__(
        self,
        api_key: str,
        user_agent: str = "books-metadata-agent/1.0",
        session: requests.Session | None = None,
    ):
        self._api_key = api_key
        self._user_agent = user_agent
        self._session = session or requests.Session()
        self._call_count = 0
        # Caches
        self._volume_search_cache: dict[str, list[dict[str, Any]]] = {}
        self._volume_cache: dict[int, dict[str, Any]] = {}
        self._issues_for_volume_cache: dict[int, list[dict[str, Any]]] = {}
        self._issue_cache: dict[int, dict[str, Any]] = {}

    @property
    def call_count(self) -> int:
        """How many live API calls this instance has made. For budget logging."""
        return self._call_count

    # ---- queries ----------------------------------------------------------

    def search_volumes(
        self,
        name: str,
        year_range: tuple[int, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Search for volumes matching a series name via /search/.

        Uses /search/?resources=volume rather than /volumes/?filter=name:
        — the latter silently drops legitimate hits (returns 0 results
        for queries the search endpoint resolves to 2000+ hits).

        year_range filters client-side; cache key is name-only so different
        year ranges share one API call.
        """
        cache_key = name.lower().strip()
        if cache_key not in self._volume_search_cache:
            body = self._request(
                "/search/",
                query=name,
                resources="volume",
                limit=100,
            )
            results = body.get("results") or []
            # Defensive: /search/ honors `resources=` but cheap to enforce.
            results = [r for r in results if r.get("resource_type") == "volume"]
            self._volume_search_cache[cache_key] = [_simplify_volume(v) for v in results]
        candidates = self._volume_search_cache[cache_key]
        if year_range is not None:
            lo, hi = year_range
            candidates = [
                v for v in candidates
                if _year_in_range(v.get("start_year"), lo, hi)
            ]
        return candidates

    def get_volume(self, volume_id: int) -> dict[str, Any]:
        """Fetch a single volume by numeric id."""
        if volume_id in self._volume_cache:
            return self._volume_cache[volume_id]
        body = self._request(f"/volume/{self.VOLUME_RESOURCE_PREFIX}{volume_id}/")
        result = body.get("results") or {}
        simplified = _simplify_volume(result)
        self._volume_cache[volume_id] = simplified
        return simplified

    def get_issues_for_volume(self, volume_id: int) -> list[dict[str, Any]]:
        """List issues belonging to a volume."""
        if volume_id in self._issues_for_volume_cache:
            return self._issues_for_volume_cache[volume_id]
        body = self._request(
            "/issues/",
            filter=f"volume:{volume_id}",
            limit=100,
            sort="issue_number:asc",
        )
        results = body.get("results") or []
        simplified = [_simplify_issue(i) for i in results]
        self._issues_for_volume_cache[volume_id] = simplified
        return simplified

    def get_issue(self, issue_id: int) -> dict[str, Any]:
        """Fetch a single issue by numeric id."""
        if issue_id in self._issue_cache:
            return self._issue_cache[issue_id]
        body = self._request(f"/issue/{self.ISSUE_RESOURCE_PREFIX}{issue_id}/")
        result = body.get("results") or {}
        simplified = _simplify_issue(result)
        self._issue_cache[issue_id] = simplified
        return simplified

    # ---- internals --------------------------------------------------------

    def _request(self, path: str, **params: Any) -> dict[str, Any]:
        """GET with api_key + json format + required User-Agent. Raises
        typed exceptions on rate limit, auth, or API-level errors."""
        params["api_key"] = self._api_key
        params["format"] = "json"
        url = f"{self.BASE_URL}{path}"
        headers = {"User-Agent": self._user_agent}
        resp = self._session.get(url, params=params, headers=headers, timeout=20)
        self._call_count += 1
        if resp.status_code in (429, 420):
            raise ComicVineRateLimitError(
                f"Rate limit (HTTP {resp.status_code}) on {path}"
            )
        if resp.status_code == 401 or resp.status_code == 403:
            raise ComicVineAuthError(
                f"Auth failed (HTTP {resp.status_code}) on {path}"
            )
        if resp.status_code != 200:
            raise ComicVineAPIError(f"HTTP {resp.status_code} on {path}")
        body = resp.json() or {}
        body_status = body.get("status_code")
        if body_status == 107:
            raise ComicVineRateLimitError(
                f"Rate limit (status_code=107) on {path}"
            )
        if body_status != 1:
            raise ComicVineAPIError(
                f"API error on {path}: status_code={body_status}, error={body.get('error')!r}"
            )
        return body


# ---- response simplifiers -------------------------------------------------


def _simplify_volume(raw: dict[str, Any]) -> dict[str, Any]:
    publisher = raw.get("publisher") or {}
    image = raw.get("image") or {}
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "start_year": raw.get("start_year"),
        "publisher": publisher.get("name"),
        "count_of_issues": raw.get("count_of_issues"),
        "description_html": raw.get("description"),
        "image_url": image.get("original_url"),
        "site_url": raw.get("site_detail_url"),
    }


def _simplify_issue(raw: dict[str, Any]) -> dict[str, Any]:
    volume = raw.get("volume") or {}
    image = raw.get("image") or {}
    return {
        "id": raw.get("id"),
        "issue_number": raw.get("issue_number"),
        "name": raw.get("name"),
        "cover_date": raw.get("cover_date"),
        "store_date": raw.get("store_date"),
        "volume_id": volume.get("id"),
        "volume_name": volume.get("name"),
        "image_url": image.get("original_url"),
        "site_url": raw.get("site_detail_url"),
    }


def _year_in_range(start_year: Any, lo: int, hi: int) -> bool:
    """ComicVine returns start_year as a string. Parse permissively — if
    the value is unparseable, exclude from year-filtered results rather
    than include; the agent can fall back to unfiltered search if needed."""
    if start_year is None:
        return False
    try:
        y = int(str(start_year).strip()[:4])
    except ValueError:
        return False
    return lo <= y <= hi
