from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse

from models import SearchResult, utc_now_iso


class SearchProvider(ABC):
    name = "base"

    @abstractmethod
    def search(self, query: str, top_k: int) -> list[SearchResult]:
        raise NotImplementedError


class StaticSearchProvider(SearchProvider):
    """테스트와 로컬 데모용 Provider."""

    name = "static"

    def __init__(self, results_by_query: dict[str, list[dict]] | None = None):
        self.results_by_query = results_by_query or {}

    def search(self, query: str, top_k: int) -> list[SearchResult]:
        rows = self.results_by_query.get(query, [])[:top_k]
        return [
            SearchResult(
                query=query,
                title=row.get("title", ""),
                url=row.get("url", ""),
                domain=row.get("domain") or _domain(row.get("url", "")),
                snippet=row.get("snippet", ""),
                content=row.get("content", ""),
                fetched_at=row.get("fetched_at") or utc_now_iso(),
            )
            for row in rows
        ]


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")

