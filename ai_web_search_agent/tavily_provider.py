from __future__ import annotations

from urllib.parse import urlparse

import httpx

from models import SearchResult, utc_now_iso
from providers import SearchProvider


class TavilySearchProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, top_k: int) -> list[SearchResult]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": top_k,
            "include_answer": False,
            "include_raw_content": False,
        }
        with httpx.Client(timeout=20.0) as client:
            response = client.post("https://api.tavily.com/search", json=payload)
            response.raise_for_status()
            data = response.json()

        results = []
        fetched_at = utc_now_iso()
        for row in data.get("results", []):
            url = row.get("url", "")
            results.append(
                SearchResult(
                    query=query,
                    title=row.get("title", ""),
                    url=url,
                    domain=urlparse(url).netloc.lower().removeprefix("www."),
                    snippet=row.get("content", ""),
                    content=row.get("raw_content") or "",
                    fetched_at=fetched_at,
                )
            )
        return results

