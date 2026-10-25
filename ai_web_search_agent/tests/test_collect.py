"""⑥ stage 1 cases from docs/6-agent-websearch.md §4 (1, 2, 6~10, 13, 14, 17, 18, 20, 22)."""

import asyncio
import time
from datetime import datetime, timezone

from ai_supervisor.contracts.web_search import WebCollectRequest
from ai_web_search_agent import (
    RecipeReading,
    SearchProvider,
    SearchResult,
    StaticSearchProvider,
    WebCollectAgent,
)

QUERY = "김치찌개 레시피"
FETCHED_AT = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)
RECIPE_TEXT = "재료: 돼지고기 200g, 두부 1/2모, 양파 1개, 대파 1대"


def result(n: int, content: str = RECIPE_TEXT, title: str = "김치찌개 황금레시피") -> SearchResult:
    return SearchResult(
        query=QUERY,
        title=title,
        url=f"https://www.10000recipe.com/recipe/{n}",
        content=content,
        fetched_at=FETCHED_AT,
    )


def provider(results: list[SearchResult]) -> StaticSearchProvider:
    return StaticSearchProvider({QUERY: results})


def request(*, next_rank: int = 1, cached: list[SearchResult] = ()) -> WebCollectRequest:
    return WebCollectRequest.model_validate(
        {
            "context": {
                "schema_version": "3.0",
                "trace_id": "0199a2f0-0000-7000-8000-000000000001",
                "scan_session_id": "scan-123",
                "store_id": 123456,
                "call_scope": "item",
                "item_id": "scan-123:0",
            },
            "menu": {"menu_id": None, "normalized_menu_name": "김치찌개", "locale_hint": "ko"},
            "max_sources": 10,
            "cached_sources": [
                {
                    "source_url": r.url,
                    "title": r.title,
                    "fetched_at": "2026-10-01T00:00:00Z",
                    "evidence": {
                        "evidence_id": f"web:{r.url}",
                        "evidence_class": "soft",
                        "source_type": "web_search",
                        "source_ref": r.url,
                        "verification_status": "pending_review",
                        "reliability_weight": None,
                        "observed_at": "2026-10-01T00:00:00Z",
                    },
                    "ingredients": [{"ingredient_id": None, "canonical_name": "두부"}],
                }
                for r in cached
            ],
            "next_rank": next_rank,
            "warnings": [],
            "errors": [],
        }
    )


class Reader:
    """Fake LLM: drops results whose title is in ``drop``, adds ``extra`` names."""

    def __init__(self, drop: set[str] = frozenset(), extra: list[str] = ()) -> None:
        self.drop = drop
        self.extra = list(extra)
        self.calls = 0

    async def read(self, menu_name, results):
        self.calls += 1
        return [
            RecipeReading(is_menu_recipe=r.title not in self.drop, ingredients=self.extra)
            for r in results
        ]


class FailingReader:
    async def read(self, menu_name, results):
        raise RuntimeError("llm down")


class SlowReader:
    async def read(self, menu_name, results):
        await asyncio.sleep(1)
        return []


class FailingProvider(SearchProvider):
    def search(self, query, start, count, sites=()):
        raise ConnectionError("search api down")


def run(agent: WebCollectAgent, req: WebCollectRequest):
    return asyncio.run(agent.run(req))


def codes(signals) -> list[str]:
    return [s.code for s in signals]


def test_01_success_returns_sources_with_names_as_written() -> None:
    response = run(WebCollectAgent(provider([result(i) for i in range(10)]), Reader()), request())

    assert response.found is True
    assert len(response.sources) == 10
    # The title is read too: "김치찌개" gives 김치.
    assert [i.canonical_name for i in response.sources[0].ingredients] == [
        "김치", "돼지고기", "두부", "양파", "대파",
    ]
    assert all(i.ingredient_id is None for i in response.sources[0].ingredients)
    assert response.next_rank == 11
    assert response.warnings == [] and response.errors == []


def test_06_evidence_is_unreviewed_soft_web_search() -> None:
    response = run(WebCollectAgent(provider([result(0)]), Reader()), request())

    evidence = response.sources[0].evidence
    assert evidence.evidence_id == "web:https://www.10000recipe.com/recipe/0"
    assert (evidence.evidence_class, evidence.source_type) == ("soft", "web_search")
    assert evidence.verification_status == "pending_review"
    assert evidence.reliability_weight is None


def test_02_14_nothing_found_without_cache_is_no_information() -> None:
    for agent in (
        WebCollectAgent(FailingProvider(), Reader()),
        WebCollectAgent(provider([]), Reader()),
    ):
        response = run(agent, request())

        assert response.found is False
        assert response.sources == []
        assert response.next_rank == 1
        error = response.errors[0]
        assert (error.code, error.retryable, error.fallback) == (
            "web_search_timeout", True, "no_information",
        )


def test_07_20_dropped_results_are_replaced_by_next_ones() -> None:
    results = [result(i, title="김치찜 레시피" if i < 3 else "김치찌개") for i in range(15)]
    reader = Reader(drop={"김치찜 레시피"})

    response = run(WebCollectAgent(provider(results), reader), request())

    assert len(response.sources) == 10
    assert all(s.title == "김치찌개" for s in response.sources)
    assert response.next_rank == 14
    assert reader.calls == 2


def test_results_without_ingredients_are_not_valid_sources() -> None:
    results = [result(0, content="분위기 좋은 곳이에요", title="맛집 방문 후기"), result(1)]

    response = run(WebCollectAgent(provider(results), Reader()), request())

    assert [s.source_url for s in response.sources] == ["https://www.10000recipe.com/recipe/1"]


def test_08_09_llm_names_are_kept_only_when_in_the_text() -> None:
    text = RECIPE_TEXT + ", 마라소스 2큰술"
    reader = Reader(extra=["마라소스", "소고기"])

    response = run(WebCollectAgent(provider([result(0, content=text)]), reader), request())

    names = [i.canonical_name for i in response.sources[0].ingredients]
    assert names == ["김치", "돼지고기", "두부", "양파", "대파", "마라소스"]


def test_10_22_llm_failure_keeps_every_result_with_dictionary_names() -> None:
    results = [result(0, title="김치찜 레시피"), result(1)]
    for reader in (FailingReader(), SlowReader(), None):
        agent = WebCollectAgent(provider(results), reader, llm_seconds=0.05)

        response = run(agent, request())

        assert len(response.sources) == 2
        assert codes(response.warnings) == ["web_llm_extraction_failed"]
        assert response.warnings[0].forces_caution is True


def test_13_deadline_mid_search_adds_warning() -> None:
    class HalfThenSlow(SearchProvider):
        def search(self, query, start, count, sites=()):
            if start == 1:
                return [result(i, title="김치찜 레시피" if i < 7 else "김치찌개") for i in range(count)]
            time.sleep(0.5)
            return []

    agent = WebCollectAgent(
        HalfThenSlow(), Reader(drop={"김치찜 레시피"}), total_seconds=0.2
    )

    response = run(agent, request())

    assert len(response.sources) == 3
    assert codes(response.warnings) == ["web_search_deadline_exceeded"]
    assert response.next_rank == 11


def test_17_cache_reads_next_ranks_and_returns_only_new_sources() -> None:
    results = [result(i) for i in range(20)]

    response = run(
        WebCollectAgent(provider(results), Reader()),
        request(next_rank=11, cached=results[:10]),
    )

    assert [s.source_url for s in response.sources] == [r.url for r in results[10:20]]
    assert response.next_rank == 21


def test_cached_url_is_not_counted_again() -> None:
    results = [result(0), result(1)]

    response = run(
        WebCollectAgent(provider(results), Reader()), request(cached=[results[0]])
    )

    assert [s.source_url for s in response.sources] == [results[1].url]
    assert response.next_rank == 3


def test_18_search_failure_with_cache_is_not_an_error() -> None:
    response = run(
        WebCollectAgent(FailingProvider(), Reader()),
        request(next_rank=11, cached=[result(0)]),
    )

    assert response.found is False
    assert response.errors == []
    assert response.next_rank == 11


def test_input_signals_are_kept() -> None:
    req = WebCollectRequest.model_validate(
        {
            **request().model_dump(mode="json"),
            "warnings": [
                {
                    "code": "store_context_missing",
                    "node": "supervisor",
                    "item_id": "scan-123:0",
                    "message": "가게 정보가 없어요",
                    "forces_caution": True,
                }
            ],
        }
    )

    response = run(WebCollectAgent(provider([result(0)]), Reader()), req)

    assert codes(response.warnings) == ["store_context_missing"]
    assert response.context == req.context
