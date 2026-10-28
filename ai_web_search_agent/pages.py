"""Read the page body of each kept search result (docs/6-agent-websearch.md §2-3-1).

Search APIs return only a title and a short description, which rarely holds the
ingredient list. The three priority sites embed schema.org ``Recipe`` data (JSON-LD
``recipeIngredient``), so their ingredient list is read from there; the page text would
also pick up food names from site menus. Other pages fall back to their visible text.
Naver blog posts sit in an iframe, so the mobile page is read instead.

A page that cannot be read keeps its title and description only.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .collect import PAGE_SECONDS
from .providers import SearchResult

MAX_CONTENT_CHARS = 10_000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
}
_JSON_LD = re.compile(
    r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
_SPACES = re.compile(r"\s+")


def readable_url(url: str) -> str:
    """The URL whose HTML holds the post: Naver blog posts move to ``m.blog.naver.com``."""

    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"blog.naver.com", "www.blog.naver.com"}:
        return url
    query = parse_qs(parsed.query)
    if "blogId" in query and "logNo" in query:
        return f"https://m.blog.naver.com/{query['blogId'][0]}/{query['logNo'][0]}"
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) == 2:
        return f"https://m.blog.naver.com/{parts[0]}/{parts[1]}"
    return url


def _recipes(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [r for item in data for r in _recipes(item)]
    if not isinstance(data, dict):
        return []
    if "@graph" in data:
        return _recipes(data["@graph"])
    kind = data.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return [data] if "Recipe" in kinds else []


def recipe_ingredients(page: str) -> list[str]:
    """``recipeIngredient`` lines of the page's schema.org Recipe data, as written."""

    for block in _JSON_LD.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for recipe in _recipes(data):
            lines = recipe.get("recipeIngredient") or []
            names = [str(line).strip() for line in lines if str(line).strip()]
            if names:
                return names
    return []


class _VisibleText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "header", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self.skipping and data.strip():
            self.parts.append(data.strip())


def visible_text(page: str) -> str:
    parser = _VisibleText()
    parser.feed(page)
    return _SPACES.sub(" ", " ".join(parser.parts)).strip()


def page_content(page: str) -> str:
    ingredients = recipe_ingredients(page)
    if ingredients:
        return "재료: " + ", ".join(ingredients)
    return visible_text(page)[:MAX_CONTENT_CHARS]


class HttpPageFetcher:
    """Fills ``SearchResult.content`` from the page. ``collect.py`` reads pages at once."""

    def __init__(self, client: httpx.Client | None = None, *, timeout: float = PAGE_SECONDS):
        self.client = client or httpx.Client(
            timeout=timeout, follow_redirects=True, headers=HEADERS
        )

    def fetch(self, result: SearchResult) -> SearchResult:
        try:
            response = self.client.get(readable_url(result.url))
            response.raise_for_status()
        except httpx.HTTPError:
            return result
        content = page_content(response.text)
        return replace(result, content=content) if content else result
