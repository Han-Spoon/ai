"""Search providers for ⑥ stage 1 (docs/6-agent-websearch.md §2-3-1, §2-5).

``NaverSearchProvider`` calls the Naver Search API on NAVER API HUB (NAVER Cloud
Platform). ``StaticSearchProvider`` is for tests and local runs without a key.
"""

from __future__ import annotations

import html
import os
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


@dataclass(frozen=True)
class SearchResult:
    query: str
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    fetched_at: datetime = field(default_factory=_utc_now)

    @property
    def domain(self) -> str:
        return domain_of(self.url)

    def text(self) -> str:
        return " ".join(part for part in (self.title, self.snippet, self.content) if part)


class SearchProvider(ABC):
    name = "base"

    @abstractmethod
    def search(
        self, query: str, start: int, count: int, sites: tuple[str, ...] = ()
    ) -> list[SearchResult]:
        """Results at ranks ``start`` .. ``start + count - 1`` (1-based) of one ranked list.

        ``sites`` limits the search to those domains; empty means the whole web. Fewer
        than ``count`` results means the list has ended. Errors are raised, not hidden.
        """
        raise NotImplementedError


class StaticSearchProvider(SearchProvider):
    """Returns canned results per query; for tests and runs without a search key."""

    name = "static"

    def __init__(self, results_by_query: dict[str, list[SearchResult]] | None = None):
        self.results_by_query = results_by_query or {}

    def search(
        self, query: str, start: int, count: int, sites: tuple[str, ...] = ()
    ) -> list[SearchResult]:
        results = self.results_by_query.get(query, [])
        if sites:
            results = [r for r in results if r.domain in sites]
        return results[start - 1 : start - 1 + count]


class SearchNotConfiguredError(RuntimeError):
    """No search key is set (a dev or test run), so nothing was searched."""


class UnconfiguredSearchProvider(SearchProvider):
    """Stands in when no search key is set. Every search raises instead of returning an
    empty list, so "never searched" is not mistaken for "searched and found nothing" (#120)."""

    name = "unconfigured"

    def search(
        self, query: str, start: int, count: int, sites: tuple[str, ...] = ()
    ) -> list[SearchResult]:
        raise SearchNotConfiguredError(
            "NAVER_SEARCH_CLIENT_ID and NAVER_SEARCH_CLIENT_SECRET are not set"
        )


NAVER_API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/{kind}"
NAVER_MAX_DISPLAY = 100
NAVER_TIMEOUT_SECONDS = 3.0
_TAG = re.compile(r"<[^>]+>")


class NaverSearchConfigError(ValueError):
    """The optional Naver search environment is invalid."""


def _plain(text: str) -> str:
    """Naver wraps matched words in ``<b>`` and escapes entities."""

    return html.unescape(_TAG.sub("", text or "")).strip()


class NaverSearchProvider(SearchProvider):
    """Naver Search API (§2-3-1 "검색 방법").

    With ``sites``, each site is searched as web documents with ``site:{domain}`` and the
    per-site lists are merged in turn (1st of each, then 2nd of each, ...) into one ranked
    list. ``site:`` lets other domains through, so callers still check the domain. Without
    ``sites`` the general web is the blog search: web documents without ``site:`` repeat
    the priority sites, and ``-site:`` does not work.

    Only titles and short descriptions come back; page bodies are read by ``pages.py``.
    """

    name = "naver"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = NAVER_TIMEOUT_SECONDS,
    ) -> None:
        self.client = client or httpx.Client(timeout=timeout)
        self.headers = {
            "X-NCP-APIGW-API-KEY-ID": client_id,
            "X-NCP-APIGW-API-KEY": client_secret,
        }

    @classmethod
    def from_env_if_configured(cls, **kwargs: Any) -> "NaverSearchProvider | None":
        client_id = os.getenv("NAVER_SEARCH_CLIENT_ID", "").strip()
        client_secret = os.getenv("NAVER_SEARCH_CLIENT_SECRET", "").strip()
        if not client_id and not client_secret:
            return None
        if not client_id or not client_secret:
            raise NaverSearchConfigError(
                "NAVER_SEARCH_CLIENT_ID and NAVER_SEARCH_CLIENT_SECRET must be set together"
            )
        return cls(client_id, client_secret, **kwargs)

    def search(
        self, query: str, start: int, count: int, sites: tuple[str, ...] = ()
    ) -> list[SearchResult]:
        if not sites:
            return self._call("blog", query, start, min(count, NAVER_MAX_DISPLAY))
        # One ranked list over several sites: re-read each site from rank 1, deep enough
        # for ``start + count - 1`` merged results, and slice. Naver returns 100 at most.
        display = min(start + count - 1, NAVER_MAX_DISPLAY)
        with ThreadPoolExecutor(max_workers=len(sites)) as pool:
            per_site = list(pool.map(
                lambda site: self._call("webkr", f"site:{site} {query}", 1, display), sites
            ))
        merged = [
            results[i]
            for i in range(max(map(len, per_site), default=0))
            for results in per_site
            if i < len(results)
        ]
        return merged[start - 1 : start - 1 + count]

    def _call(self, kind: str, query: str, start: int, display: int) -> list[SearchResult]:
        response = self.client.get(
            NAVER_API_URL.format(kind=kind),
            params={"query": query, "start": start, "display": display},
            headers=self.headers,
        )
        response.raise_for_status()
        return [
            SearchResult(
                query=query,
                title=_plain(item.get("title", "")),
                url=item["link"],
                snippet=_plain(item.get("description", "")),
            )
            for item in response.json().get("items", [])
            if item.get("link")
        ]


def search_provider_from_env(**kwargs: Any) -> SearchProvider:
    """Naver when its key is set, otherwise ``UnconfiguredSearchProvider``."""

    return NaverSearchProvider.from_env_if_configured(**kwargs) or UnconfiguredSearchProvider()
