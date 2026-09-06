"""MangaBaka v2 API client for the metadata agent.

Narrow surface focused on what the agent needs for manga matching:
  - Search for candidate series by title.
  - Fetch a single series for more detail (cover URL, publisher, year).

MangaBaka models series only — there is no separate "issue" record like
ComicVine has. A matched series_id IS the volume_id IS the issue_id from
the agent's perspective. Per-volume issue numbers come from the source
filename (handled later by comictagger -f); they're not in MangaBaka.

Endpoints (api.mangabaka.org/v2/):
  - GET /series/search?q=<q>&page=&limit= → {pagination, data: [series]}
  - GET /series/<id>                      → {data: series}

API quirk: the search query param is `q`. Older code (and an early read of
their docs) sent `title=`; the API now rejects that with HTTP 400 +
`{"message":"Validation error: Unrecognized key: \"title\""}`.

v2 replaced the flat `title`/`native_title`/`romanized_title`/`secondary_titles`
fields with a single `titles` list of {language, traits, title, is_primary},
and `year` with a `published` object. The output shape below is unchanged, so
callers keep the ComicVine-derived keys they already read.

Rate limit: 60/min — generous, won't bite at our cadence. No auth required.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import requests


class MangaBakaAPIError(Exception):
    """Non-rate-limit error (network, bad response, 5xx)."""


class MangaBakaRateLimitError(Exception):
    """Rate limit exceeded. Caller should back off."""


class MangaBakaClient:
    BASE_URL = "https://api.mangabaka.org/v2/"

    def __init__(
        self,
        user_agent: str = "comics-metadata-agent/1.0",
        session: requests.Session | None = None,
    ):
        self._user_agent = user_agent
        self._session = session or requests.Session()
        self._call_count = 0
        # Caches
        self._search_cache: dict[str, list[dict[str, Any]]] = {}
        self._series_cache: dict[int, dict[str, Any]] = {}

    @property
    def call_count(self) -> int:
        return self._call_count

    def search_series(
        self,
        title: str,
        max_pages: int = 3,
    ) -> list[dict[str, Any]]:
        """Search MangaBaka by title. Returns simplified series dicts.

        Walks up to `max_pages` pages of paginated results to broaden the
        candidate pool. Cached per query string for the run.
        """
        cache_key = title.strip().lower()
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        results: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            body = self._request(
                "series/search",
                q=title,
                page=page,
                limit=50,
            )
            data = body.get("data") or []
            results.extend(_simplify_series(s) for s in data)
            pagination = body.get("pagination") or {}
            if not pagination.get("next"):
                break
            page += 1

        self._search_cache[cache_key] = results
        return results

    def get_series(self, series_id: int) -> dict[str, Any]:
        """Fetch a single series by id."""
        if series_id in self._series_cache:
            return self._series_cache[series_id]
        body = self._request(f"series/{series_id}")
        result = body.get("data") or {}
        simplified = _simplify_series(result)
        self._series_cache[series_id] = simplified
        return simplified

    def _request(self, path: str, **params: Any) -> dict[str, Any]:
        """GET against the MangaBaka API. Raises typed errors on rate
        limit / auth / network failure."""
        url = urljoin(self.BASE_URL, path)
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        resp = self._session.get(
            url,
            params={k: v for k, v in params.items() if v is not None},
            headers=headers,
            timeout=20,
        )
        self._call_count += 1
        if resp.status_code == 429:
            raise MangaBakaRateLimitError(f"Rate limit (HTTP 429) on {path}")
        if resp.status_code != 200:
            raise MangaBakaAPIError(f"HTTP {resp.status_code} on {path}")
        try:
            body = resp.json()
        except ValueError as e:
            raise MangaBakaAPIError(f"non-JSON response on {path}: {e}") from e
        if not isinstance(body, dict):
            raise MangaBakaAPIError(f"unexpected response shape on {path}: {type(body)}")
        return body


def _simplify_series(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize an MBSeries response to fields the agent + planner use.

    Notable mapping: the start of `published` becomes `start_year` so it lines
    up with the ComicVine-derived shape used elsewhere. `count_of_issues` is
    derived from `total_chapters` when available.
    """
    titles = raw.get("titles") or []
    name = _get_title(titles, "en")
    native_title, romanized_title = _native_titles(titles)
    cover = raw.get("cover") or {}
    publishers = raw.get("publishers") or []
    publisher_name = None
    if publishers:
        first = publishers[0]
        if isinstance(first, dict):
            publisher_name = first.get("name")
        else:
            publisher_name = str(first)

    total_chapters = raw.get("total_chapters")
    try:
        count_of_issues = int(total_chapters) if total_chapters else None
    except (TypeError, ValueError):
        count_of_issues = None

    return {
        "id": raw.get("id"),
        "name": name,
        "native_title": native_title,
        "romanized_title": romanized_title,
        "aliases": _extract_aliases(titles, name),
        "start_year": _start_year(raw.get("published")),
        "publisher": publisher_name,
        # Full list with type+note preserved so the planner can filter
        # by licensee criteria (type=English, real note) rather than
        # just exposing the first entry.
        "publishers": raw.get("publishers") or [],
        "count_of_issues": count_of_issues,
        "description_html": raw.get("description"),
        "image_url": _pick_cover_url(cover),
        "site_url": raw.get("canonical_url"),
        "type": raw.get("type"),  # manga, novel, manhwa, manhua, oel, other
        "status": raw.get("status"),
        "content_rating": raw.get("content_rating"),
    }


def _get_title(titles: list[Any], lang: str = "en") -> str | None:
    """Pick the display title for a language, mirroring the upstream talker's
    precedence so both agree on what a series is called."""
    entries = [t for t in titles if isinstance(t, dict)]
    in_lang = [t for t in entries if str(t.get("language") or "").startswith(lang)]
    for candidate in (
        [t for t in in_lang if t.get("is_primary")],
        [t for t in in_lang if "official" in (t.get("traits") or [])],
        [t for t in in_lang if "alternative" in (t.get("traits") or [])],
        in_lang,
        entries,
    ):
        if candidate:
            return candidate[0].get("title")
    return None


def _native_titles(titles: list[Any]) -> tuple[str | None, str | None]:
    """Split the `native` trait into (native, romanized). A BCP-47 -Latn
    subtag is the romanization, e.g. ja is 'BLEACH' and ja-Latn is its
    transliteration."""
    native = romanized = None
    for t in titles:
        if not isinstance(t, dict) or "native" not in (t.get("traits") or []):
            continue
        if str(t.get("language") or "").endswith("-Latn"):
            romanized = romanized or t.get("title")
        else:
            native = native or t.get("title")
    return native, romanized


def _extract_aliases(titles: list[Any], primary: str | None) -> list[str]:
    """Every other name in `titles`, deduped and excluding the display title.
    Alternate names MB knows for the series but doesn't track as separate
    records — useful for recognizing variant editions like "<Series> Omnibus"."""
    out: list[str] = []
    seen: set[str] = set()
    if primary:
        seen.add(primary)
    for entry in titles:
        if not isinstance(entry, dict):
            continue
        t = entry.get("title")
        if t and t not in seen:
            out.append(t)
            seen.add(t)
    return out


def _start_year(published: Any) -> int | None:
    """v2 carries a `published` range; the agent only wants the start year."""
    if not isinstance(published, dict):
        return None
    start = published.get("start_date")
    if not isinstance(start, str) or len(start) < 4:
        return None
    try:
        return int(start[:4])
    except ValueError:
        return None


def _pick_cover_url(cover: dict[str, Any]) -> str | None:
    """MangaBaka's cover schema has nested raw/default/small or x250 etc.
    Pick the highest-resolution URL we can find."""
    if not isinstance(cover, dict):
        return None
    for key in ("raw", "default"):
        url = cover.get(key)
        if isinstance(url, str):
            return url
    # Newer schema has nested {x250: {x1: url, x2: url}} etc.
    for size in cover.values():
        if isinstance(size, dict):
            for url in size.values():
                if isinstance(url, str):
                    return url
    return None
