"""⑥ stage 1: search, keep this menu's recipes, pull ingredient names (docs/6-agent-websearch.md §2-1, §3).

No DB and no tags. Each new recipe comes back with the ingredient names as written;
④ maps them between stage 1 and stage 2. Not here yet:

- Widening to the general web when the priority sites run out (§2-3-1). Which search
  ``next_rank`` then points into is still open (§5), so only priority sites are read.

The LLM sits behind ``RecipeReader`` (OpenAI version in ``reader.py``). Without a reader,
or when it fails, every result is kept and only dictionary names are used, with
``web_llm_extraction_failed``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

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
from .providers import SearchProvider, SearchResult

NODE = "web_search"
TOTAL_SECONDS = 7.0
SEARCH_SECONDS = 3.0
LLM_SECONDS = 3.0


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
    read_count: int = 0
    timed_out: bool = False
    search_failed: bool = False
    llm_failed: bool = False


class WebCollectAgent:
    """⑥ stage 1 node, matching the Supervisor ``NodeAdapter`` shape."""

    def __init__(
        self,
        provider: SearchProvider,
        reader: RecipeReader | None = None,
        *,
        total_seconds: float = TOTAL_SECONDS,
        search_seconds: float = SEARCH_SECONDS,
        llm_seconds: float = LLM_SECONDS,
    ) -> None:
        self.provider = provider
        self.reader = reader
        self.total_seconds = total_seconds
        self.search_seconds = search_seconds
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
            next_rank=request.next_rank + collection.read_count,
            warnings=warnings,
            errors=errors,
        )

    async def _collect(self, request: WebCollectRequest) -> _Collection:
        """Read priority-site results from ``next_rank`` until ``max_sources`` are valid.

        A result is valid when it is kept as this menu's recipe and has at least one
        ingredient name (§2-3). Dropped results are replaced by the next ones.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.total_seconds
        menu_name = request.menu.normalized_menu_name
        query = search_query(menu_name)
        seen = {source.source_url for source in request.cached_sources}
        collection = _Collection(sources=[])
        rank = request.next_rank

        while len(collection.sources) < request.max_sources:
            remaining = deadline - loop.time()
            if remaining <= 0:
                collection.timed_out = True
                break
            wanted = request.max_sources - len(collection.sources)
            try:
                batch = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.provider.search, query, rank, wanted, PRIORITY_DOMAINS
                    ),
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

            fresh = [r for r in batch if r.url not in seen]
            seen.update(r.url for r in batch)
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
                if names and len(collection.sources) < request.max_sources:
                    collection.sources.append(to_web_source(result, names))
            if len(batch) < wanted:
                break
        return collection

    async def _read(
        self,
        menu_name: str,
        results: list[SearchResult],
        deadline: float,
        collection: _Collection,
    ) -> list[RecipeReading | None]:
        """LLM readings per result, or ``None`` for each when the LLM is missing or fails."""

        if not results:
            return []
        fallback: list[RecipeReading | None] = [None] * len(results)
        remaining = deadline - asyncio.get_running_loop().time()
        if self.reader is None or remaining <= 0:
            collection.llm_failed = True
            return fallback
        try:
            readings = await asyncio.wait_for(
                self.reader.read(menu_name, results),
                timeout=min(self.llm_seconds, remaining),
            )
        except Exception:
            collection.llm_failed = True
            return fallback
        if len(readings) != len(results):
            collection.llm_failed = True
            return fallback
        return list(readings)
