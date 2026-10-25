"""Search provider interface for ⑥ stage 1 (docs/6-agent-websearch.md §2-3-1, §2-5).

The real provider is the Naver Search API (#254). Until then only the static provider
exists, for tests and local runs without a key.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse


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
