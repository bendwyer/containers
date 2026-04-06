#!/usr/bin/env python3
import os; os.umask(0o022)  # noqa: E702 — ensure files are group/world-readable
"""Fetch metadata from the Kobo store or Goodreads and enrich epub ebooks.

Supports two metadata providers:
  --provider kobo       Search the Kobo store (uses cloudscraper)
  --provider goodreads  Scrape Goodreads (uses requests)

Forked from https://github.com/NotSimone/Kobo-Metadata (Calibre plugin)
and made standalone by replacing Calibre dependencies with ebooklib + stdlib.
Goodreads scraper adapted from https://github.com/kiwidude68/calibre_plugins.

Created with Claude Opus 4.5

Usage:
    ebook_metadata.py --input-dir /books/sources/kobo \
                      --output-dir /books/library/ebooks \
                      --marker-dir /books/markers/kobo \
                      --provider kobo --country us --language en \
                      --skip-large 50
"""

import argparse
import json
import logging
import os
import re
import shutil
import string
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

import cloudscraper
import requests
from ebooklib import epub
from lxml import html

log = logging.getLogger("ebook-metadata")


# ---------------------------------------------------------------------------
# BookMetadata dataclass — replaces calibre.ebooks.metadata.book.base.Metadata
# ---------------------------------------------------------------------------
@dataclass
class BookMetadata:
    title: str = ""
    authors: List[str] = field(default_factory=list)
    publisher: str = ""
    pubdate: Optional[datetime] = None
    isbn: str = ""
    language: str = ""
    series: str = ""
    series_index: str = ""
    tags: set = field(default_factory=set)
    comments: str = ""
    cover_url: str = ""
    source_relevance: int = 0


# ---------------------------------------------------------------------------
# Helpers — replace calibre utility functions
# ---------------------------------------------------------------------------
def check_isbn(isbn: Optional[str]) -> Optional[str]:
    """Validate an ISBN-10 or ISBN-13 string."""
    if isbn is None:
        return None
    cleaned = str(isbn).strip().replace("-", "").replace(" ", "")
    if re.fullmatch(r"\d{10}(\d{3})?", cleaned):
        return cleaned
    return None


def fixauthors(authors: List[str]) -> List[str]:
    """Strip whitespace from author names."""
    return [a.strip() for a in authors if a]


def parse_only_date(date_str: str) -> Optional[datetime]:
    """Parse a date string into a datetime object."""
    for fmt in ("%B %d, %Y", "%Y-%m-%d", "%d %B %Y", "%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def get_title_tokens(title: str) -> List[str]:
    """Tokenize a title string, stripping punctuation."""
    if not title:
        return []
    # Remove punctuation and split on whitespace
    cleaned = title.translate(str.maketrans("", "", string.punctuation))
    return [t for t in cleaned.split() if t]


def get_author_tokens(authors: List[str]) -> List[str]:
    """Tokenize author names."""
    tokens = []
    for author in authors:
        cleaned = author.translate(str.maketrans("", "", string.punctuation))
        tokens.extend(cleaned.split())
    return [t for t in tokens if t]


# ---------------------------------------------------------------------------
# KoboMetadata — core search and parse logic (kept as-is from the plugin)
# ---------------------------------------------------------------------------
class KoboMetadata:
    BASE_URL = "https://www.kobo.com/"
    session: Optional[requests.Session] = None

    def __init__(self, country: str = "us", language: str = "en"):
        self.prefs: Dict[str, any] = {
            "country": country,
            "language": language,
            "num_matches": 3,
            "remove_leading_zeroes": True,
            "resize_cover": False,
            "title_blacklist": "",
            "tag_blacklist": "",
        }

    def get_search_url(self, search_str: str, page_number: int) -> str:
        query = {
            "query": search_str,
            "fcmedia": "Book",
            "pageNumber": page_number,
            "fclanguages": self.prefs["language"],
        }
        return f"{self.BASE_URL}{self.prefs['country']}/{self.prefs['language']}/search?{urlencode(query)}"

    def get_kobo_url(self, kobo_id: str) -> str:
        if self.prefs["language"] == "all":
            return f"{self.BASE_URL}{self.prefs['country']}/ebook/{kobo_id}"
        return f"{self.BASE_URL}{self.prefs['country']}/{self.prefs['language']}/ebook/{kobo_id}"

    def identify(self, title: str, authors: List[str], identifiers: Dict[str, any], timeout: int = 30) -> List[BookMetadata]:
        """Search the Kobo store and return matching metadata results."""
        log.info(f"identify: title={title}, authors={authors}, identifiers={identifiers}")

        id_urls = []
        isbn = check_isbn(identifiers.get("isbn", None))
        kobo = identifiers.get("kobo", None)

        if kobo:
            log.info(f"Searching with Kobo ID: {kobo}")
            id_urls.append(self.get_kobo_url(kobo))

        if isbn:
            log.info(f"Searching with ISBN: {isbn}")
            id_urls.extend(self._perform_isbn_search(isbn, self.prefs["num_matches"], timeout))

        if id_urls:
            unique_id_urls = list(dict.fromkeys(id_urls))
            fetched = self._fetch_metadata(unique_id_urls, timeout)
            if fetched:
                log.info(f"Found {len(fetched)} match(es) using identifiers")
                return fetched

        log.info("No matches with identifiers, falling back to general search")
        search_urls = self._perform_search(title, authors, self.prefs["num_matches"], timeout)

        if search_urls:
            unique_urls = list(dict.fromkeys(search_urls))
            fetched = self._fetch_metadata(unique_urls, timeout)
            if fetched:
                log.info(f"Found {len(fetched)} match(es) using general search")
                return fetched

        return []

    def get_cover(self, cover_url: str, timeout: int = 30) -> bytes:
        session = self._get_session()
        return session.get(cover_url, timeout=timeout).content

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = cloudscraper.create_scraper(
                browser={
                    "custom": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
                },
                interpreter="v8",
                ecdhCurve="secp384r1",
            )
        return self.session

    def _get_webpage(self, url: str, timeout: int) -> Tuple[Optional[html.HtmlElement], bool]:
        session = self._get_session()
        try:
            attempts = 0
            while attempts < 15:
                resp = session.get(url, timeout=timeout)
                page = html.fromstring(resp.text)
                if (
                    not page.xpath("//form[@class='challenge-form']")
                    and not page.xpath("//form[@id='challenge-form']")
                    and not page.xpath("//span[@id='challenge-error-text']")
                ):
                    is_search = "/search?" in resp.url
                    return (page, is_search)
                log.info(f"Could not defeat cloudflare protection - trying again for {url}")
                attempts += 1
                time.sleep(1.0)
            log.error(f"Could not defeat cloudflare protection - giving up for {url}")
            return (None, False)
        except Exception as e:
            log.error(f"Got exception while opening url: {e}")
            return (None, False)

    def _perform_isbn_search(self, isbn: str, max_matches: int, timeout: int) -> List[str]:
        isbn = check_isbn(isbn)
        if isbn:
            log.info(f"Getting metadata with isbn: {isbn}")
            return self._perform_query(isbn, max_matches, timeout)
        return []

    def _perform_search(self, title: str, authors: List[str], max_matches: int, timeout: int) -> List[str]:
        query = self._generate_query(title, authors)
        log.info(f"Searching with query: {query}")
        return self._perform_query(query, max_matches, timeout)

    def _fetch_metadata(self, urls: List[str], timeout: int) -> List[BookMetadata]:
        results = []
        for index, url in enumerate(urls):
            log.info(f"Looking up metadata with url: {url}")
            try:
                page, is_search = self._get_webpage(url, timeout)
                if page is None or is_search:
                    log.info(f"Could not get url: {url}")
                    continue
                metadata = self._parse_book_page(page)
            except Exception as e:
                log.error(f"Got exception looking up metadata: {e}")
                continue

            if metadata:
                metadata.source_relevance = index
                results.append(metadata)
            else:
                log.info("Could not find matching book")
        return results

    def _generate_query(self, title: str, authors: List[str]) -> str:
        tokens = []
        for t in get_title_tokens(title):
            if self.prefs["remove_leading_zeroes"]:
                t = t.lstrip("0")
            tokens.append(t)
        query = " ".join(tokens)

        if authors:
            query += " " + " ".join(get_author_tokens(authors))

        return query

    def _perform_query(self, query: str, max_matches: int, timeout: int) -> List[str]:
        url = self.get_search_url(query, 1)
        log.info(f"Searching for book with url: {url}")

        page, is_search = self._get_webpage(url, timeout)
        if page is None:
            log.info(f"Could not get url: {url}")
            return []

        if not is_search:
            return [url]

        results = self._parse_search_page(page)

        page_num = 2
        max_page_num = 4
        while len(results) < max_matches and page_num < max_page_num:
            url = self.get_search_url(query, page_num)
            page, is_search = self._get_webpage(url, timeout)
            if page is None or not is_search:
                break
            results.extend(self._parse_search_page(page))
            page_num += 1

        return results[:max_matches]

    def _parse_search_page(self, page: html.HtmlElement) -> List[str]:
        if len(page.xpath("//div[@data-testid='search-result-widget']")):
            log.info("Detected new search page")
            result_elements = page.xpath("//a[@data-testid='title']")
            return [x.get("href") for x in result_elements[::2]]

        result_elements = page.xpath("//h2[@class='title product-field']/a")
        if len(result_elements):
            log.info("Detected old search page")
            return [x.get("href") for x in result_elements]

        log.error("Found no matches or bad page")
        return []

    def _parse_book_page(self, page: html.HtmlElement) -> Optional[BookMetadata]:
        title_elements = page.xpath("//h1[@class='title product-field']")
        if not title_elements:
            return None
        title = title_elements[0].text.strip()
        log.info(f"Got title: {title}")

        authors_elements = page.xpath("//span[@class='visible-contributors']/a")
        authors = fixauthors([x.text for x in authors_elements])
        log.info(f"Got authors: {authors}")

        metadata = BookMetadata(title=title, authors=authors)

        series_elements = page.xpath("//span[@class='series product-field']")
        if series_elements:
            series_name_element = series_elements[-1].xpath("span[@class='product-sequence-field']/a")
            if series_name_element:
                metadata.series = series_name_element[0].text
                log.info(f"Got series: {metadata.series}")

            series_index_element = series_elements[-1].xpath("span[@class='sequenced-name-prefix']")
            if series_index_element:
                series_index_match = re.match("Book (.*) - ", series_index_element[0].text)
                if series_index_match:
                    metadata.series_index = series_index_match.groups(0)[0]
                    log.info(f"Got series_index: {metadata.series_index}")

        book_details_elements = page.xpath("//div[@class='bookitem-secondary-metadata']/ul/li")
        if book_details_elements:
            metadata.publisher = book_details_elements[0].text.strip()
            log.info(f"Got publisher: {metadata.publisher}")
            for x in book_details_elements[1:]:
                descriptor = x.text.strip()
                if descriptor == "Release Date:":
                    metadata.pubdate = parse_only_date(x.xpath("span")[0].text)
                    log.info(f"Got pubdate: {metadata.pubdate}")
                elif descriptor in ("ISBN:", "Book ID:"):
                    metadata.isbn = x.xpath("span")[0].text
                    log.info(f"Got isbn: {metadata.isbn}")
                elif descriptor == "Language:":
                    metadata.language = x.xpath("span")[0].text
                    log.info(f"Got language: {metadata.language}")

        tags_elements = page.xpath("//ul[@class='category-rankings']/meta[@property='genre']")
        if tags_elements:
            metadata.tags = {x.get("content").replace(", ", " ") for x in tags_elements}
            log.info(f"Got tags: {metadata.tags}")

        synopsis_elements = page.xpath("//div[@data-full-synopsis='']")
        if synopsis_elements:
            metadata.comments = synopsis_elements[0].text_content()
            log.info(f"Got comments: {metadata.comments[:80]}...")

        cover_url = self._parse_book_page_for_cover(page)
        if cover_url:
            metadata.cover_url = cover_url

        blacklisted_title = self._check_title_blacklist(title)
        if blacklisted_title:
            log.info(f"Hit blacklisted word(s) in the title: {blacklisted_title}")
            return None

        blacklisted_tags = self._check_tag_blacklist(metadata.tags)
        if blacklisted_tags:
            log.info(f"Hit blacklisted tag(s): {blacklisted_tags}")
            return None

        return metadata

    def _parse_book_page_for_cover(self, page: html.HtmlElement) -> str:
        cover_elements = page.xpath("//img[contains(@class, 'cover-image')]")
        if not cover_elements:
            return ""
        cover_url = "https:" + cover_elements[0].get("src")
        # Get original cover (remove resize parameters)
        cover_url = cover_url.replace("353/569/90/False/", "")
        log.info(f"Got cover: {cover_url}")
        return cover_url

    def _check_title_blacklist(self, title: str) -> Optional[set]:
        if not self.prefs["title_blacklist"]:
            return None
        blacklisted_words = {x.strip().lower() for x in self.prefs["title_blacklist"].split(",")}
        title_str = title.translate(str.maketrans("", "", string.punctuation))
        result = blacklisted_words.intersection(title_str.lower().split(" "))
        return result if result else None

    def _check_tag_blacklist(self, tags: set) -> Optional[set]:
        if not self.prefs["tag_blacklist"] or not tags:
            return None
        blacklisted_tags = {x.strip().lower() for x in self.prefs["tag_blacklist"].split(",")}
        result = blacklisted_tags.intersection({x.lower() for x in tags})
        return result if result else None


# ---------------------------------------------------------------------------
# GoodreadsMetadata — scrape Goodreads for metadata
# Adapted from calibre_plugins/goodreads/ (Grant Drake, GPL v3)
# ---------------------------------------------------------------------------
class GoodreadsMetadata:
    BASE_URL = "https://www.goodreads.com"
    AUTOCOMPLETE_URL = "https://www.goodreads.com/book/auto_complete?format=json&q="
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    LANG_MAP: Dict[str, str] = {}
    _LANG_NAMES = {
        "eng": ("English", "Englisch"),
        "fra": ("French", "Français"),
        "ita": ("Italian", "Italiano"),
        "dut": ("Dutch",),
        "deu": ("German", "Deutsch"),
        "spa": ("Spanish", "Español", "Espaniol"),
        "jpn": ("Japanese", "日本語"),
        "por": ("Portuguese", "Português"),
    }

    def __init__(self):
        self.session: Optional[requests.Session] = None
        # Build lang_map from _LANG_NAMES
        if not GoodreadsMetadata.LANG_MAP:
            for code, names in self._LANG_NAMES.items():
                for name in names:
                    GoodreadsMetadata.LANG_MAP[name] = code

    def identify(self, title: str, authors: List[str], identifiers: Dict[str, any], timeout: int = 30) -> List[BookMetadata]:
        """Search Goodreads and return matching metadata results."""
        log.info(f"goodreads identify: title={title}, authors={authors}, identifiers={identifiers}")

        isbn = check_isbn(identifiers.get("isbn", None))

        # Fast path: ISBN via autocomplete API
        if isbn:
            goodreads_id = self._get_id_via_api(isbn, timeout)
            if goodreads_id:
                result = self._fetch_book_details(goodreads_id, timeout)
                if result:
                    result.source_relevance = 0
                    return [result]

        # Search by title + author
        matches = self._search_books(title, authors, timeout)
        if not matches:
            return []

        results = []
        for index, book_url in enumerate(matches[:3]):
            gid = self._parse_goodreads_id(book_url)
            if not gid:
                continue
            result = self._fetch_book_details(gid, timeout)
            if result:
                result.source_relevance = index
                results.append(result)

        return results

    def get_cover(self, cover_url: str, timeout: int = 30) -> bytes:
        session = self._get_session()
        resp = session.get(cover_url, timeout=timeout)
        resp.raise_for_status()
        return resp.content

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": self.USER_AGENT})
        return self.session

    def _get_id_via_api(self, identifier: str, timeout: int) -> Optional[str]:
        """Use the Goodreads autocomplete API to resolve an ISBN to a book ID."""
        session = self._get_session()
        url = self.AUTOCOMPLETE_URL + identifier
        try:
            log.info(f"Goodreads autocomplete API: {url}")
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if data and len(data) >= 1:
                book_id = data[0].get("bookId")
                if book_id:
                    log.info(f"Goodreads autocomplete found bookId={book_id}")
                    return str(book_id)
        except Exception as e:
            log.warning(f"Goodreads autocomplete API failed: {e}")
        return None

    def _search_books(self, title: str, authors: List[str], timeout: int) -> List[str]:
        """Search Goodreads by title+author and return book page URLs."""
        tokens = []
        tokens.extend(get_title_tokens(title))
        if authors:
            # Only use first author for search
            tokens.extend(get_author_tokens(authors[:1]))
        if not tokens:
            return []

        encoded_tokens = [quote(t) for t in tokens]
        query = "+".join(encoded_tokens)
        url = f"{self.BASE_URL}/search?search_type=books&search[query]={query}"
        log.info(f"Goodreads search: {url}")

        session = self._get_session()
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
        except Exception as e:
            log.error(f"Goodreads search failed: {e}")
            return []

        root = html.fromstring(resp.text)

        # Parse search results table
        result_cells = root.xpath('//table[@class="tableList"]/tr/td[2]')
        if not result_cells:
            log.info("No Goodreads search results found")
            return []

        title_tokens = [t.lower() for t in get_title_tokens(title)]
        author_tokens = [t.lower() for t in get_author_tokens(authors)]

        matches = []
        for cell in result_cells:
            result_title_els = cell.xpath("./a")
            if not result_title_els:
                continue
            result_title = result_title_els[0].text_content().strip()

            result_author_els = cell.xpath('.//span[@itemprop="author"]//a/span')
            result_authors = result_author_els[0].text_content().strip() if result_author_els else ""

            # Skip Large Print / audio editions
            if "Large Print" in result_title:
                continue

            # Token match
            rt_lower = result_title.lower()
            ra_lower = result_authors.lower()
            title_match = not title_tokens or any(t in rt_lower for t in title_tokens)
            author_match = not author_tokens or any(a in ra_lower for a in author_tokens)

            if title_match and author_match:
                href = result_title_els[0].get("href", "")
                if href:
                    full_url = self.BASE_URL + href if href.startswith("/") else href
                    matches.append(full_url)
                    log.info(f"Goodreads match: {result_title} by {result_authors}")
                    if len(matches) >= 3:
                        break

        return matches

    @staticmethod
    def _parse_goodreads_id(url: str) -> Optional[str]:
        m = re.search(r"/show/(\d+)", url)
        return m.group(1) if m else None

    def _fetch_book_details(self, goodreads_id: str, timeout: int) -> Optional[BookMetadata]:
        """Fetch and parse a Goodreads book page using __NEXT_DATA__ JSON."""
        url = f"{self.BASE_URL}/book/show/{goodreads_id}"
        session = self._get_session()

        for attempt in range(3):
            try:
                log.info(f"Fetching Goodreads book page: {url} (attempt {attempt + 1})")
                resp = session.get(url, timeout=timeout)
                resp.raise_for_status()
            except Exception as e:
                log.error(f"Failed to fetch Goodreads book page: {e}")
                return None

            root = html.fromstring(resp.text)
            script_nodes = root.xpath('//script[@id="__NEXT_DATA__"]')
            if not script_nodes:
                log.info("No __NEXT_DATA__ found, retrying")
                time.sleep(1.0)
                continue

            try:
                props = json.loads(script_nodes[0].text)
                apollo_state = props["props"]["pageProps"]["apolloState"]
                if not apollo_state:
                    log.info("Empty apolloState, retrying")
                    time.sleep(1.0)
                    continue
            except (json.JSONDecodeError, KeyError) as e:
                log.error(f"Failed to parse __NEXT_DATA__: {e}")
                return None

            return self._parse_apollo_state(apollo_state)

        log.error(f"Failed to get valid book data after retries for {url}")
        return None

    def _parse_apollo_state(self, apollo_state: dict) -> Optional[BookMetadata]:
        """Extract metadata from the Goodreads apolloState dict."""
        book_json = None
        series_json = None
        contributors = []
        work_json = None

        for key, value in apollo_state.items():
            if key.startswith("Book:") and "title" in value:
                book_json = value
            elif key.startswith("Series:") and series_json is None:
                series_json = value
            elif key.startswith("Contributor:"):
                contributors.append(value)
            elif key.startswith("Work:"):
                work_json = value

        if not book_json or "title" not in book_json:
            return None

        title = book_json["title"]
        log.info(f"Goodreads title: {title}")

        # Authors
        authors = self._parse_authors(book_json, contributors)
        if not authors:
            return None
        log.info(f"Goodreads authors: {authors}")

        metadata = BookMetadata(title=title, authors=authors)

        # Series
        if series_json and "title" in series_json:
            metadata.series = series_json["title"]
            if "bookSeries" in book_json:
                for bs in book_json["bookSeries"]:
                    if "userPosition" in bs:
                        try:
                            val = float(bs["userPosition"])
                            metadata.series_index = str(int(val)) if val == int(val) else str(val)
                        except (ValueError, TypeError):
                            pass
                        break
            log.info(f"Goodreads series: {metadata.series} #{metadata.series_index}")

        # ISBN
        details = book_json.get("details", {})
        isbn13 = details.get("isbn13")
        isbn10 = details.get("isbn")
        metadata.isbn = isbn13 or isbn10 or ""
        if metadata.isbn:
            log.info(f"Goodreads ISBN: {metadata.isbn}")

        # Publisher
        publisher = details.get("publisher")
        if publisher:
            metadata.publisher = publisher
            log.info(f"Goodreads publisher: {metadata.publisher}")

        # Publication date
        epoch_time = details.get("publicationTime")
        if work_json and "details" in work_json:
            work_pub = work_json["details"].get("publicationTime")
            if work_pub:
                epoch_time = work_pub
        if epoch_time:
            try:
                metadata.pubdate = datetime.fromtimestamp(int(epoch_time) // 1000, tz=__import__("datetime").timezone.utc)
                log.info(f"Goodreads pubdate: {metadata.pubdate}")
            except (ValueError, TypeError, OSError):
                pass

        # Language
        lang_data = details.get("language", {})
        if isinstance(lang_data, dict):
            lang_name = lang_data.get("name", "")
            lang_code = self.LANG_MAP.get(lang_name)
            if lang_code:
                metadata.language = lang_code
                log.info(f"Goodreads language: {metadata.language}")

        # Description
        description = book_json.get("description")
        if description:
            # Strip HTML tags
            metadata.comments = re.sub(r"<[^>]+>", "", description).strip()
            if metadata.comments:
                log.info(f"Goodreads comments: {metadata.comments[:80]}...")

        # Tags/genres
        if "bookGenres" in book_json:
            tags = set()
            for bg in book_json["bookGenres"]:
                genre = bg.get("genre", {})
                name = genre.get("name")
                if name:
                    tags.add(name)
            if tags:
                metadata.tags = tags
                log.info(f"Goodreads tags: {metadata.tags}")

        # Cover
        img_url = book_json.get("imageUrl", "")
        if img_url:
            metadata.cover_url = img_url
            log.info(f"Goodreads cover: {metadata.cover_url}")

        return metadata

    @staticmethod
    def _parse_authors(book_json: dict, contributors: list) -> List[str]:
        """Extract author names from book_json + contributor nodes."""
        author_ref_ids = []
        primary = book_json.get("primaryContributorEdge")
        if not primary:
            return []

        role = primary.get("role", "")
        if role in ("Author", "Pseudonym"):
            ref = primary.get("node", {}).get("__ref", "")
            if ref.startswith("Contributor:"):
                author_ref_ids.append(ref[len("Contributor:"):])

        for secondary in book_json.get("secondaryContributorEdges", []):
            if secondary.get("role") == "Author":
                ref = secondary.get("node", {}).get("__ref", "")
                if ref.startswith("Contributor:"):
                    author_ref_ids.append(ref[len("Contributor:"):])

        # Fallback: if no Author role found, use primary contributor
        if not author_ref_ids:
            ref = primary.get("node", {}).get("__ref", "")
            if ref.startswith("Contributor:"):
                author_ref_ids.append(ref[len("Contributor:"):])

        authors = []
        for contrib in contributors:
            cid = contrib.get("id", "")
            if cid in author_ref_ids and contrib.get("name"):
                authors.append(contrib["name"])

        return fixauthors(authors)


# ---------------------------------------------------------------------------
# EPUB read / write helpers
# ---------------------------------------------------------------------------

# OPF XML namespaces
_OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    """Locate the OPF file inside an epub zip via META-INF/container.xml."""
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        rootfile = container.find(".//c:rootfile", ns)
        if rootfile is not None:
            return rootfile.get("full-path", "")
    except (KeyError, ET.ParseError):
        pass
    # Fallback: look for any .opf file
    for name in zf.namelist():
        if name.endswith(".opf"):
            return name
    return ""


def read_epub_metadata(epub_path: Path) -> Dict[str, any]:
    """Read title, authors, and ISBN from an epub file by parsing the OPF directly.

    This avoids ebooklib's read_epub which crashes on epubs with missing
    manifest entries (common in Kobo and Project Gutenberg epubs).
    """
    with zipfile.ZipFile(str(epub_path), "r") as zf:
        opf_path = _find_opf_path(zf)
        if not opf_path:
            return {"title": "", "authors": [], "isbn": ""}

        opf_xml = zf.read(opf_path)
        root = ET.fromstring(opf_xml)

    # Title
    title = ""
    title_el = root.find(".//dc:title", _OPF_NS)
    if title_el is not None and title_el.text:
        title = title_el.text.strip()

    # Authors
    authors = []
    for creator_el in root.findall(".//dc:creator", _OPF_NS):
        if creator_el.text:
            authors.append(creator_el.text.strip())

    # ISBN
    isbn = ""
    for ident_el in root.findall(".//dc:identifier", _OPF_NS):
        if ident_el.text is None:
            continue
        value = ident_el.text.strip()
        scheme = ident_el.get("{http://www.idpf.org/2007/opf}scheme", "").upper()
        if scheme == "ISBN" or check_isbn(value):
            checked = check_isbn(value)
            if checked:
                isbn = checked
                break

    # Series (EPUB 3.2 belongs-to-collection)
    series = ""
    series_index = ""
    OPF = "http://www.idpf.org/2007/opf"
    metadata_el = root.find(f"{{{OPF}}}metadata")
    if metadata_el is not None:
        for el in metadata_el:
            prop = el.get("property", "")
            if prop == "belongs-to-collection" and el.text:
                series = el.text.strip()
            elif prop == "group-position" and el.text:
                series_index = el.text.strip()

    return {"title": title, "authors": authors, "isbn": isbn, "series": series, "series_index": series_index}


def _epub_has_cover(epub_path: Path) -> bool:
    """Check if an epub already contains a cover image."""
    try:
        with zipfile.ZipFile(str(epub_path), "r") as zf:
            opf_path = _find_opf_path(zf)
            if not opf_path:
                return False
            root = ET.fromstring(zf.read(opf_path))
            OPF = "http://www.idpf.org/2007/opf"
            manifest = root.find(f"{{{OPF}}}manifest")
            if manifest is None:
                return False
            for item in manifest.findall(f"{{{OPF}}}item"):
                props = item.get("properties", "")
                item_id = item.get("id", "")
                media = item.get("media-type", "")
                if "cover-image" in props or item_id in ("cover-image", "cover-img", "coverimg"):
                    return True
            # Also check for <meta name="cover" content="..."> referencing a manifest item
            metadata = root.find(f"{{{OPF}}}metadata")
            if metadata is not None:
                for meta in metadata:
                    if meta.get("name") == "cover" and meta.get("content"):
                        return True
    except Exception:
        pass
    return False


def _read_epub_tolerant(epub_path: Path) -> epub.EpubBook:
    """Read an epub with ebooklib, tolerating missing manifest entries.

    Monkey-patches the reader's read_file method so that missing zip entries
    return empty bytes instead of raising KeyError.
    """
    reader = epub.EpubReader(str(epub_path))
    original_read_file = reader.read_file

    def tolerant_read_file(name):
        try:
            return original_read_file(name)
        except KeyError:
            log.warning(f"  Missing file in epub archive: {name} (skipping)")
            return b""

    reader.read_file = tolerant_read_file
    reader.process()
    return reader.book


def write_epub_metadata(epub_path: Path, metadata: BookMetadata, cover_data: Optional[bytes] = None) -> None:
    """Update metadata in an epub file by directly editing the OPF XML inside the zip.

    This avoids ebooklib's lossy read/write round-trip which strips manifest entries.
    """
    import xml.etree.ElementTree as ET
    import tempfile

    DC = "http://purl.org/dc/elements/1.1/"
    OPF = "http://www.idpf.org/2007/opf"
    ET.register_namespace("dc", DC)
    ET.register_namespace("opf", OPF)
    ET.register_namespace("", OPF)

    with zipfile.ZipFile(str(epub_path), "r") as zin:
        # Find the OPF file
        opf_path = None
        for name in zin.namelist():
            if name.endswith(".opf"):
                opf_path = name
                break
        if opf_path is None:
            # Try container.xml
            try:
                container = ET.fromstring(zin.read("META-INF/container.xml"))
                ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
                rootfile = container.find(".//c:rootfile", ns)
                if rootfile is not None:
                    opf_path = rootfile.get("full-path")
            except Exception:
                pass
        if opf_path is None:
            raise ValueError("Cannot find OPF file in epub")

        opf_data = zin.read(opf_path)
        tree = ET.ElementTree(ET.fromstring(opf_data))
        root = tree.getroot()
        meta_el = root.find(f"{{{OPF}}}metadata")
        if meta_el is None:
            raise ValueError("Cannot find <metadata> in OPF")

        # Helper: remove all children with given tag from metadata
        def _remove_dc(tag):
            for el in meta_el.findall(f"{{{DC}}}{tag}"):
                meta_el.remove(el)

        def _set_dc(tag, value):
            _remove_dc(tag)
            el = ET.SubElement(meta_el, f"{{{DC}}}{tag}")
            el.text = value

        # Update DC metadata
        if metadata.title:
            _set_dc("title", metadata.title)

        if metadata.authors:
            _remove_dc("creator")
            for author in metadata.authors:
                el = ET.SubElement(meta_el, f"{{{DC}}}creator")
                el.text = author

        if metadata.publisher:
            _set_dc("publisher", metadata.publisher)

        if metadata.comments:
            _set_dc("description", metadata.comments)

        if metadata.pubdate:
            _set_dc("date", metadata.pubdate.strftime("%Y-%m-%d"))

        if metadata.language:
            _set_dc("language", metadata.language)

        if metadata.tags:
            _remove_dc("subject")
            for tag in sorted(metadata.tags):
                el = ET.SubElement(meta_el, f"{{{DC}}}subject")
                el.text = tag

        # Update series (EPUB 3.2 belongs-to-collection)
        if metadata.series:
            # Remove existing collection meta elements
            for el in list(meta_el):
                if el.tag == "meta" or el.tag == f"{{{OPF}}}meta":
                    prop = el.get("property", "")
                    if prop in ("belongs-to-collection", "collection-type", "group-position"):
                        meta_el.remove(el)

            col_el = ET.SubElement(meta_el, "meta")
            col_el.set("property", "belongs-to-collection")
            col_el.set("id", "series-id")
            col_el.text = metadata.series

            type_el = ET.SubElement(meta_el, "meta")
            type_el.set("property", "collection-type")
            type_el.set("refines", "#series-id")
            type_el.text = "series"

            if metadata.series_index:
                pos_el = ET.SubElement(meta_el, "meta")
                pos_el.set("property", "group-position")
                pos_el.set("refines", "#series-id")
                pos_el.text = metadata.series_index

        # Determine cover image path in the zip for replacement
        cover_image_path = None
        if cover_data:
            manifest = root.find(f"{{{OPF}}}manifest")
            if manifest is not None:
                # Look for item with properties="cover-image" or id containing "cover"
                opf_dir = str(Path(opf_path).parent)
                for item in manifest.findall(f"{{{OPF}}}item"):
                    props = item.get("properties", "")
                    item_id = item.get("id", "")
                    if "cover-image" in props or item_id in ("cover-image", "cover-img"):
                        href = item.get("href", "")
                        if opf_dir and opf_dir != ".":
                            cover_image_path = f"{opf_dir}/{href}"
                        else:
                            cover_image_path = href
                        break

        # Serialize updated OPF
        new_opf = ET.tostring(root, encoding="unicode", xml_declaration=True)

        # Rewrite the zip with updated OPF (and optionally cover)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".epub")
        os.close(tmp_fd)
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.filename == opf_path:
                        zout.writestr(item, new_opf)
                    elif cover_data and cover_image_path and item.filename == cover_image_path:
                        # Check if new cover is larger
                        existing = zin.read(item.filename)
                        if len(cover_data) > len(existing):
                            zout.writestr(item, cover_data)
                        else:
                            log.info(f"  Keeping existing cover ({len(existing)} bytes >= new {len(cover_data)} bytes)")
                            zout.writestr(item, existing)
                    else:
                        zout.writestr(item, zin.read(item.filename))

            shutil.move(tmp_path, str(epub_path))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


def sanitize_path_component(name: str) -> str:
    """Sanitize a string for use as a filesystem path component."""
    # Replace problematic characters
    name = name.translate(str.maketrans("/:\\", "___"))
    # Remove leading/trailing whitespace and dots
    name = name.strip().strip(".")
    return name if name else "Unknown"


def _pick_best_result(results: List[BookMetadata], title: str, authors: List[str]) -> BookMetadata:
    """Pick the best metadata result, preferring complete series info and title match."""
    if len(results) == 1:
        return results[0]

    title_lower = title.lower().strip()
    author_set = {a.lower().strip() for a in authors} if authors else set()

    def _score(m: BookMetadata) -> tuple:
        # Exact title match
        t_match = 1 if m.title and m.title.lower().strip() == title_lower else 0
        # Author overlap
        a_match = 1 if m.authors and {a.lower().strip() for a in m.authors} & author_set else 0
        # Has series + index (most important for ordering)
        has_series = 1 if m.series else 0
        has_index = 1 if m.series_index else 0
        # Has cover
        has_cover = 1 if m.cover_url else 0
        # Has ISBN
        has_isbn = 1 if m.isbn else 0
        return (t_match, a_match, has_series + has_index, has_isbn, has_cover)

    results.sort(key=_score, reverse=True)
    return results[0]


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------
def process_epub(epub_path: Path, searcher, output_dir: Path, marker_dir: Path) -> bool:
    """Process a single epub file: fetch metadata, update, and organize."""
    name = epub_path.name
    marker = marker_dir / f"{name}.enriched"

    if marker.exists():
        log.debug(f"Skipping (already processed): {name}")
        return False

    log.info(f"Processing: {name}")

    # Read existing metadata from epub
    try:
        existing = read_epub_metadata(epub_path)
    except Exception as e:
        log.error(f"  Failed to read epub metadata: {e}")
        return False

    title = existing["title"]
    authors = existing["authors"]
    isbn = existing["isbn"]

    if not title:
        log.warning(f"  No title found in {name}, skipping")
        return False

    embedded_series = existing.get("series", "")
    embedded_series_index = existing.get("series_index", "")

    log.info(f"  Existing: title={title}, authors={authors}, isbn={isbn}"
             + (f", series={embedded_series} #{embedded_series_index}" if embedded_series else ""))

    # Search metadata provider
    identifiers = {}
    if isbn:
        identifiers["isbn"] = isbn

    results = searcher.identify(title, authors, identifiers)

    if not results:
        author_str = ", ".join(authors) if authors else "Unknown"
        log.warning(f"  NO MATCH: {title} by {author_str}")
        # Still organize the file with existing metadata
        best = BookMetadata(
            title=title,
            authors=authors if authors else ["Unknown Author"],
        )
    else:
        best = _pick_best_result(results, title, authors)
        log.info(f"  Match: title={best.title}, authors={best.authors}, series={best.series}")

    # Fill in missing series/index from epub embedded metadata
    if not best.series and embedded_series:
        best.series = embedded_series
        log.info(f"  Using embedded series: {embedded_series}")
    if best.series and not best.series_index and embedded_series_index:
        best.series_index = embedded_series_index
        log.info(f"  Using embedded series index: {embedded_series_index}")

    # Fallback to Goodreads for missing series_index
    if best.series and not best.series_index and not isinstance(searcher, GoodreadsMetadata):
        log.info(f"  Series index missing from epub and {searcher.__class__.__name__}, falling back to Goodreads")
        try:
            gr = GoodreadsMetadata()
            gr_results = gr.identify(best.title, best.authors, {})
            if gr_results:
                gr_best = _pick_best_result(gr_results, best.title, best.authors)
                if gr_best.series and gr_best.series_index:
                    log.info(f"  Goodreads fallback: series={gr_best.series} #{gr_best.series_index}")
                    best.series_index = gr_best.series_index
                    # Also adopt Goodreads series name if the primary had none
                    if not best.series:
                        best.series = gr_best.series
                else:
                    log.warning(f"  Goodreads fallback: no series_index found either")
        except Exception as e:
            log.warning(f"  Goodreads fallback failed: {e}")

    # Download cover only if the epub doesn't already have one
    cover_data = None
    if best.cover_url:
        if _epub_has_cover(epub_path):
            log.info("  Skipping cover download (epub already has a cover)")
        else:
            try:
                cover_data = searcher.get_cover(best.cover_url)
                log.info(f"  Downloaded cover ({len(cover_data)} bytes)")
            except Exception as e:
                log.warning(f"  Failed to download cover: {e}")

    # Write enriched metadata back to epub
    try:
        write_epub_metadata(epub_path, best, cover_data)
        log.info(f"  Updated epub metadata")
    except Exception as e:
        log.error(f"  Failed to write epub metadata: {e}")
        return False

    # Organize into output-dir/Author/Series/NN - Title.epub or Author/Title.epub
    author_name = best.authors[0] if best.authors else "Unknown Author"
    safe_author = sanitize_path_component(author_name)
    safe_title = sanitize_path_component(best.title or title)
    if best.series:
        safe_series = sanitize_path_component(best.series)
        dest_dir = output_dir / safe_author / safe_series
        if best.series_index:
            parts = best.series_index.split(".", 1)
            padded = parts[0].zfill(2) + ("." + parts[1] if len(parts) > 1 else "")
            filename = f"{padded} - {safe_title}.epub"
        else:
            filename = f"{safe_title}.epub"
    else:
        dest_dir = output_dir / safe_author
        filename = f"{safe_title}.epub"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename

    try:
        shutil.copy(str(epub_path), str(dest_path))
        log.info(f"  -> {dest_path.relative_to(output_dir)}")
    except Exception as e:
        log.error(f"  Failed to copy file: {e}")
        return False

    # Create marker for idempotency — skip if series is known but index is missing
    # so the book is retried on the next run (e.g. after a Goodreads timeout)
    if best.series and not best.series_index:
        log.warning(f"  Skipping marker (series index still missing, will retry next run)")
    else:
        marker.touch()
    return True


def main():
    parser = argparse.ArgumentParser(description="Fetch metadata and enrich epub ebooks")
    parser.add_argument("--input-dir", required=True, help="Directory with epub files")
    parser.add_argument("--output-dir", required=True, help="Output directory for organized ebooks")
    parser.add_argument("--marker-dir", required=True, help="Directory to store processing markers")
    parser.add_argument("--provider", choices=["kobo", "goodreads"], default="kobo",
                        help="Metadata provider (default: kobo)")
    parser.add_argument("--country", default="us", help="Kobo store country code (default: us)")
    parser.add_argument("--language", default="en", help="Kobo store language code (default: en)")
    parser.add_argument("--skip-large", type=int, default=0,
                        help="Skip files larger than N megabytes (0 = no limit)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP request timeout in seconds (default: 30)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    marker_dir = Path(args.marker_dir)

    if not input_dir.is_dir():
        log.error(f"Input directory does not exist: {input_dir}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    marker_dir.mkdir(parents=True, exist_ok=True)

    epubs = sorted(input_dir.glob("*.epub"))
    if not epubs:
        print("No epub files found")
        return

    if args.provider == "goodreads":
        searcher = GoodreadsMetadata()
    else:
        searcher = KoboMetadata(country=args.country, language=args.language)

    log.info(f"Using provider: {args.provider}")

    processed = 0
    skipped = 0
    failed = 0
    size_skipped = 0

    for epub_path in epubs:
        marker = marker_dir / f"{epub_path.name}.enriched"
        if marker.exists():
            skipped += 1
            continue

        if args.skip_large and epub_path.stat().st_size >= args.skip_large * 1024 * 1024:
            log.debug(f"Skipping (too large): {epub_path.name}")
            size_skipped += 1
            continue

        if process_epub(epub_path, searcher, output_dir, marker_dir):
            processed += 1
        else:
            failed += 1

    print(f"\nDone: {processed} processed, {skipped} skipped, {size_skipped} too large, {failed} failed")


if __name__ == "__main__":
    main()
