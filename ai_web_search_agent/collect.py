"""⑥ stage 1: search, keep this menu's recipes, pull ingredient names (docs/6-agent-websearch.md §2-1, §3).

No DB and no tags. Each new recipe comes back with the ingredient names as written;
④ maps them between stage 1 and stage 2.

The priority sites are read first, keeping only their recipe URLs (``RECIPE_PATHS``);
when they run out before ``max_sources``, the general web fills the rest (§2-3-1). Page
bodies are read only for the results that are kept (``pages.py``).

Every search starts from rank 1; results already in the cache are skipped by URL, so no
search rank is stored or passed around (10/27 meeting, §2-4).

The LLM sits behind ``RecipeReader`` (OpenAI version in ``reader.py``). Without a reader,
or when it fails, every result is kept and only dictionary names are used, with
``web_llm_extraction_failed``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from ai_supervisor.contracts.common import (
    EvidenceRef,
    IngredientRef,
    NodeError,
    NodeWarning,
)
from ai_supervisor.contracts.external import WebSource
from ai_supervisor.contracts.web_search import (
    WebCollectRequest,
    WebCollectResponse,
    WebMenu,
)

from .aggregate import PRIORITY_DOMAINS
from .dictionary import find_ingredient_names
from .providers import SearchProvider, SearchResult, domain_of

NODE = "web_search"
TOTAL_SECONDS = 7.0
SEARCH_SECONDS = 3.0
PAGE_SECONDS = 1.5
LLM_SECONDS = 3.0
LLM_BATCH_SIZE = 3
READ_AHEAD = 2

# Recipe pages of each priority site (§2-3-1). Profiles, counseling, lists and pages of
# other domains that ``site:`` lets through are dropped before anything is read.
RECIPE_PATHS = {
    "10000recipe.com": re.compile(r"/recipe/\d+/?"),
    "wtable.co.kr": re.compile(r"/recipes/[^/]+/?"),
    "semie.cooking": re.compile(r"/(cooking/id|recipe-lab/archive|semie/archive)/[^/]+/?"),
}


def is_priority_recipe_url(url: str) -> bool:
    pattern = RECIPE_PATHS.get(domain_of(url))
    return bool(pattern and pattern.fullmatch(urlparse(url).path))


class PageFetcher(Protocol):
    def fetch(self, result: SearchResult) -> SearchResult:
        """The same result with ``content`` read from its page, or as it is."""
        ...


@dataclass(frozen=True)
class RecipeReading:
    """LLM view of one search result, in the same order as the results it was given."""

    is_menu_recipe: bool
    ingredients: list[str]


class RecipeReader(Protocol):
    async def read(self, menu_name: str, results: list[SearchResult]) -> list[RecipeReading]:
        """Judge whether each result is a recipe of ``menu_name`` and list its ingredients.

        Drop only reviews, ads, shop pages and recipes that are clearly another dish;
        keep anything unsure (§2-1 step 2).
        """
        ...


def search_query(menu_name: str) -> str:
    return f"{menu_name} 레시피"


def _compact(text: str) -> str:
    return "".join(text.split())


def merge_names(rule_names: list[str], llm_names: list[str], text: str) -> list[str]:
    """Union of dictionary and LLM names (§3-0).

    An LLM name the text does not contain is dropped as made up. Dictionary names come
    first, then names only the LLM found.
    """

    body = _compact(text)
    merged = dict.fromkeys(rule_names)
    for name in llm_names:
        name = name.strip()
        if name and _compact(name) in body:
            merged.setdefault(name)
    return list(merged)


def to_web_source(result: SearchResult, names: list[str]) -> WebSource:
    observed_at = result.fetched_at
    return WebSource(
        source_url=result.url,
        title=result.title or result.url,
        fetched_at=observed_at,
        evidence=EvidenceRef(
            evidence_id=f"web:{result.url}",
            evidence_class="soft",
            source_type="web_search",
            source_ref=result.url,
            verification_status="pending_review",
            reliability_weight=None,
            observed_at=observed_at,
        ),
        ingredients=[IngredientRef(ingredient_id=None, canonical_name=name) for name in names],
    )


@dataclass
class _Collection:
    sources: list[WebSource]
    unread_urls: set[str] = field(default_factory=set)
    read_count: int = 0
    timed_out: bool = False
    search_failed: bool = False
    llm_failed: bool = False

    def keep_read_first(self, max_sources: int) -> None:
        """Keep ``max_sources`` sources, the LLM-read ones first, in rank order.

        A result the LLM could not read is kept unread (§3 2단계), but only to fill what
        read recipes leave short: with ``READ_AHEAD`` there are usually enough of them,
        and an unread result may be another dish (2026-10-28). The LLM failure is
        reported only when an unread result is used.
        """

        read = [s for s in self.sources if s.source_url not in self.unread_urls]
        unread = [s for s in self.sources if s.source_url in self.unread_urls]
        kept = {id(s) for s in read[:max_sources] + unread[: max(0, max_sources - len(read))]}
        self.sources = [s for s in self.sources if id(s) in kept]
        self.llm_failed = any(s.source_url in self.unread_urls for s in self.sources)


class WebCollectAgent:
    """⑥ stage 1 node, matching the Supervisor ``NodeAdapter`` shape."""

    def __init__(
        self,
        provider: SearchProvider,
        reader: RecipeReader | None = None,
        *,
        pages: PageFetcher | None = None,
        total_seconds: float = TOTAL_SECONDS,
        search_seconds: float = SEARCH_SECONDS,
        page_seconds: float = PAGE_SECONDS,
        llm_seconds: float = LLM_SECONDS,
    ) -> None:
        self.provider = provider
        self.reader = reader
        self.pages = pages
        self.total_seconds = total_seconds
        self.search_seconds = search_seconds
        self.page_seconds = page_seconds
        self.llm_seconds = llm_seconds

    async def run(self, request: WebCollectRequest) -> WebCollectResponse:
        collection = await self._collect(request)
        item_id = request.context.item_id
        warnings = list(request.warnings)
        errors = list(request.errors)
        has_cache = bool(request.cached_sources)

        if collection.llm_failed and collection.read_count:
            warnings.append(NodeWarning(
                code="web_llm_extraction_failed",
                node=NODE,
                item_id=item_id,
                message="레시피 판별·재료 추출 LLM을 쓰지 못해 재료 사전 결과만 사용했어요",
                forces_caution=True,
            ))
        if collection.timed_out and (collection.sources or has_cache):
            warnings.append(NodeWarning(
                code="web_search_deadline_exceeded",
                node=NODE,
                item_id=item_id,
                message="웹 검색 시간이 초과되어 모은 레시피만 사용했어요",
                forces_caution=True,
            ))
        if not collection.sources and not has_cache:
            if collection.timed_out:
                message = "웹 검색 시간이 초과되었습니다."
            elif collection.search_failed:
                message = "웹 검색에 실패했습니다."
            else:
                message = "웹 검색으로 이 메뉴의 레시피를 찾지 못했습니다."
            errors.append(NodeError(
                code="web_search_timeout",
                node=NODE,
                item_id=item_id,
                message=message,
                retryable=True,
                fallback="no_information",
            ))

        return WebCollectResponse(
            context=request.context,
            menu=WebMenu(
                menu_id=request.menu.menu_id,
                normalized_menu_name=request.menu.normalized_menu_name,
            ),
            found=bool(collection.sources),
            sources=collection.sources,
            warnings=warnings,
            errors=errors,
        )

    async def _collect(self, request: WebCollectRequest) -> _Collection:
        """Read results from rank 1 until ``max_sources`` new ones are valid.

        The priority sites come first, then the general web (§2-3-1). A result is valid
        when it is kept as this menu's recipe and has at least one ingredient name (§2-3).
        Dropped results are replaced by the next ones. Cached URLs come back too and are
        skipped, so each batch asks for that many more (§2-4).
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.total_seconds
        menu_name = request.menu.normalized_menu_name
        query = search_query(menu_name)
        cached_urls = {source.source_url for source in request.cached_sources}
        seen = set(cached_urls)
        cached_left = len(cached_urls)
        collection = _Collection(sources=[])

        for sites in (PRIORITY_DOMAINS, ()):
            rank = 1
            while len(collection.sources) < request.max_sources:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    collection.timed_out = True
                    break
                # Read twice what is missing: one round (search, pages, LLM) takes about
                # 4.5 seconds, so a second round to replace dropped results rarely fits.
                wanted = READ_AHEAD * (request.max_sources - len(collection.sources)) + cached_left
                try:
                    batch = await asyncio.wait_for(
                        asyncio.to_thread(self.provider.search, query, rank, wanted, sites),
                        timeout=min(self.search_seconds, remaining),
                    )
                except asyncio.TimeoutError:
                    collection.timed_out = True
                    break
                except Exception:
                    collection.search_failed = True
                    break
                if not batch:
                    break
                rank += len(batch)
                collection.read_count += len(batch)

                cached_left = max(0, cached_left - sum(r.url in cached_urls for r in batch))
                fresh = [
                    r for r in batch
                    if r.url not in seen and (not sites or is_priority_recipe_url(r.url))
                ]
                seen.update(r.url for r in batch)
                fresh = await self._fetch_pages(fresh, deadline)
                readings = await self._read(menu_name, fresh, deadline, collection)
                for result, reading in zip(fresh, readings):
                    if reading is not None and not reading.is_menu_recipe:
                        continue
                    text = result.text()
                    names = merge_names(
                        find_ingredient_names(text),
                        reading.ingredients if reading is not None else [],
                        text,
                    )
                    if names:
                        collection.sources.append(to_web_source(result, names))
                        if reading is None:
                            collection.unread_urls.add(result.url)
                if len(batch) < wanted:
                    break
            if collection.timed_out or collection.search_failed:
                break
        collection.keep_read_first(request.max_sources)
        return collection

    async def _fetch_pages(
        self, results: list[SearchResult], deadline: float
    ) -> list[SearchResult]:
        """Results with their page bodies, all at once. A page that is not read in time or
        fails keeps its search title and description; the others are still used."""

        remaining = deadline - asyncio.get_running_loop().time()
        if self.pages is None or not results or remaining <= 0:
            return results
        tasks = [asyncio.ensure_future(asyncio.to_thread(self.pages.fetch, r)) for r in results]
        await asyncio.wait(tasks, timeout=min(self.page_seconds, remaining))
        fetched = []
        for result, task in zip(results, tasks):
            if task.done() and not task.cancelled() and task.exception() is None:
                fetched.append(task.result())
            else:
                task.cancel()
                fetched.append(result)
        return fetched

    async def _read(
        self,
        menu_name: str,
        results: list[SearchResult],
        deadline: float,
        collection: _Collection,
    ) -> list[RecipeReading | None]:
        """LLM readings per result, or ``None`` for each when the LLM is missing or fails.

        Results go to the LLM ``LLM_BATCH_SIZE`` at a time, all at once: one call for ten
        pages took 5.6 seconds, three-page calls side by side 2 seconds (2026-10-28). A
        failed call leaves only its own results unread.
        """

        if not results:
            return []
        remaining = deadline - asyncio.get_running_loop().time()
        if self.reader is None or remaining <= 0:
            collection.llm_failed = True
            return [None] * len(results)
        timeout = min(self.llm_seconds, remaining)
        chunks = [
            results[i : i + LLM_BATCH_SIZE] for i in range(0, len(results), LLM_BATCH_SIZE)
        ]
        outcomes = await asyncio.gather(
            *[asyncio.wait_for(self.reader.read(menu_name, chunk), timeout) for chunk in chunks],
            return_exceptions=True,
        )
        readings: list[RecipeReading | None] = []
        for chunk, outcome in zip(chunks, outcomes):
            if isinstance(outcome, BaseException) or len(outcome) != len(chunk):
                collection.llm_failed = True
                readings += [None] * len(chunk)
            else:
                readings += list(outcome)
        return readings
