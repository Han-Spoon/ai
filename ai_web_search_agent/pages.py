"""Read the page body of each kept search result (docs/6-agent-websearch.md §2-3-1).

Search APIs return only a title and a short description, which rarely holds the
ingredient list. The three priority sites embed schema.org ``Recipe`` data (JSON-LD
``recipeIngredient``), so their ingredient list is read from there; the page text would
also pick up food names from site menus. Other pages fall back to their visible text.
Naver blog posts sit in an iframe, so the mobile page is read instead.

A page that cannot be read keeps its title and description only.

Pages are read with an async client, not one thread each (2026-11-08): with five menus
at once, 100 pages queued for the default thread pool and 29~42% missed the 1.5 s page
limit (만개의레시피 then lost its ingredient list); read asynchronously, 0~1% did. A page
past the limit is also really cancelled, not left running in a thread.
"""

from __future__ import annotations

import asyncio
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


# 새미네부엌 user posts (/cooking/id/...) carry no Recipe data, and under the post the
# page lists the writer's other posts: "스크랩 댕댕이 2025.05.13 01:30 … 수제 요거트 만들기".
# Read whole, 꽃게탕 got 우유·요구르트 and 제육볶음 got 만두·된장 from those titles
# (2026-11-12 runs). The text is cut where the post ends: the comment box, the "다른 글도
# 살펴보세요" list, or the first "스크랩 {writer} {date}" line, whichever comes first.
_SEMIE_HEADER_END = "URL복사"
_OTHER_POSTS = re.compile(
    r"로그인 하시고 댓글|다른 글도 살펴보세요|스크랩 \S+ 20\d{2}\.\d{2}\.\d{2} \d{2}:\d{2}"
)


def page_content(page: str, url: str = "") -> str:
    ingredients = recipe_ingredients(page)
    if ingredients:
        return "재료: " + ", ".join(ingredients)
    text = visible_text(page)
    if urlparse(url).netloc.lower().removeprefix("www.").endswith("semie.cooking"):
        listing = _OTHER_POSTS.search(text)
        if listing:
            text = text[: listing.start()].rstrip()
        # The site menu before the post ("여러분의 부엌으로 로그인 해주세요 회원가입 홈 …
        # 공유 × 카카오톡 URL복사") took 130 of the 800 characters the LLM reads (2026-11-17).
        header = text.find(_SEMIE_HEADER_END)
        if header >= 0:
            text = text[header + len(_SEMIE_HEADER_END):].lstrip()
    return text[:MAX_CONTENT_CHARS]


class HttpPageFetcher:
    """Fills ``SearchResult.content`` from the page. ``collect.py`` reads pages at once."""

    def __init__(
        self, client: httpx.AsyncClient | None = None, *, timeout: float = PAGE_SECONDS
    ) -> None:
        self.timeout = timeout
        self._given = client
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _client_now(self) -> httpx.AsyncClient:
        """The given client, or one of our own for the running event loop.

        An async client belongs to the loop it first ran on. The app has one loop; a new
        loop (each ``asyncio.run`` in evaluation scripts) gets a new client.
        """

        if self._given is not None:
            return self._given
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            self._client = httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True, headers=HEADERS
            )
            self._loop = loop
        return self._client

    async def fetch(self, result: SearchResult) -> SearchResult:
        try:
            response = await self._client_now().get(readable_url(result.url))
            response.raise_for_status()
        except httpx.HTTPError:
            return result
        content = page_content(response.text, result.url)
        return replace(result, content=content) if content else result
