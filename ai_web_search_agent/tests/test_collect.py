"""⑥ stage 1 cases from docs/6-agent-websearch.md §4 (1, 2, 6~14, 17~22, 18-a, 18-b)."""

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timezone

from ai_supervisor.contracts.web_search import WebCollectRequest
from ai_web_search_agent.aggregate import PRIORITY_DOMAINS
from ai_web_search_agent import (
    RecipeReading,
    SearchProvider,
    SearchResult,
    StaticSearchProvider,
    UnconfiguredSearchProvider,
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


def request(*, cached: list[SearchResult] = ()) -> WebCollectRequest:
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
            "warnings": [],
            "errors": [],
        }
    )


class Reader:
    """Fake LLM: drops results whose title is in ``drop``, adds ``extra`` names."""

    def __init__(self, drop: set[str] = frozenset(), extra: list[str] = ()) -> None:
        self.drop = drop
        self.extra = list(extra)
        self.sizes: list[int] = []

    async def read(self, menu_name, results):
        self.sizes.append(len(results))
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
        error = response.errors[0]
        assert (error.code, error.retryable, error.fallback) == (
            "web_search_timeout", True, "no_information",
        )


def test_07_20_dropped_results_are_replaced_by_next_ones() -> None:
    results = [result(i, title="김치찜 레시피" if i < 12 else "김치찌개") for i in range(30)]
    reader = Reader(drop={"김치찜 레시피"})

    response = run(WebCollectAgent(provider(results), reader), request())

    assert [s.source_url for s in response.sources] == [r.url for r in results[12:22]]
    # Twice what is missing each time (20, then 4 for the 2 still missing), read three
    # at a time.
    assert reader.sizes == [3, 3, 3, 3, 3, 3, 2, 3, 1]


def test_a_failed_llm_call_leaves_only_its_own_results_unread() -> None:
    class SecondCallFails(Reader):
        async def read(self, menu_name, results):
            if self.sizes:
                self.sizes.append(len(results))
                raise RuntimeError("llm down")
            return await super().read(menu_name, results)

    results = [result(i, title="김치찜 레시피") for i in range(3)] + [result(i) for i in range(3, 6)]

    response = run(WebCollectAgent(provider(results), SecondCallFails(drop={"김치찜 레시피"})), request())

    # The first three are judged another dish; the next three are kept unread.
    assert [s.source_url for s in response.sources] == [r.url for r in results[3:]]
    assert codes(response.warnings) == ["web_llm_extraction_failed"]


class FailsOnCalls(Reader):
    """Fake LLM whose calls with these numbers (0, 1, ...) fail."""

    def __init__(self, failing: set[int]) -> None:
        super().__init__()
        self.failing = failing

    async def read(self, menu_name, results):
        call = len(self.sizes)
        self.sizes.append(len(results))
        if call in self.failing:
            raise RuntimeError("llm down")
        return [RecipeReading(is_menu_recipe=True, ingredients=[]) for _ in results]


def test_warning_only_when_half_or_more_went_unread() -> None:
    # Ten results read three at a time: calls 0..3. One failed call leaves 3 of 10
    # unread, two leave 6 of 10 (2026-11-02: warn at half or more).
    results = [result(i) for i in range(10)]

    some = run(WebCollectAgent(provider(results), FailsOnCalls({1})), request())
    most = run(WebCollectAgent(provider(results), FailsOnCalls({0, 1})), request())

    assert len(some.sources) == len(most.sources) == 10
    assert some.warnings == []
    assert codes(most.warnings) == ["web_llm_extraction_failed"]


def test_unread_results_fill_only_when_the_title_names_the_menu() -> None:
    # 2026-11-08: unread results came from the second round with no LLM time left, and
    # 5 of 6 were another dish (양념족발 for 설렁탕). Spaces do not matter.
    results = [result(i) for i in range(3)] + [
        result(3, title="양념족발 만들기"),
        result(4, title="김치 찌개 끓이는 법"),
        result(5),
    ]

    response = run(WebCollectAgent(provider(results), FailsOnCalls({1})), request())

    assert [s.source_url for s in response.sources] == [r.url for r in results if r.title != "양념족발 만들기"]
    assert response.warnings == []


def test_dish_name_in_the_title_is_not_an_ingredient() -> None:
    # 파김치 is a kind of 김치: the title alone must not add 김치 (→ 배추김치 after ④).
    req = request().model_copy(update={"menu": request().menu.model_copy(update={"normalized_menu_name": "파김치"})})
    search = StaticSearchProvider({"파김치 레시피": [result(0, content="재료: 쪽파, 멸치액젓, 고춧가루", title="파김치 담그기")]})
    names = [i.canonical_name for i in run(WebCollectAgent(search, Reader()), req).sources[0].ingredients]
    assert "김치" not in names and "멸치액젓" in names


def test_dish_name_counts_when_the_recipe_lists_it_apart() -> None:
    req = request().model_copy(update={"menu": request().menu.model_copy(update={"normalized_menu_name": "파김치"})})
    search = StaticSearchProvider({"파김치 레시피": [result(0, content="재료: 김치 양념, 쪽파", title="파김치")]})
    names = [i.canonical_name for i in run(WebCollectAgent(search, Reader()), req).sources[0].ingredients]
    assert "김치" in names


def test_menu_that_is_the_name_itself_keeps_it() -> None:
    # 순대 carries pork; a 순대 menu must not lose it.
    req = request().model_copy(update={"menu": request().menu.model_copy(update={"normalized_menu_name": "순대"})})
    search = StaticSearchProvider({"순대 레시피": [result(0, content="재료: 당면, 대파", title="순대 만들기")]})
    names = [i.canonical_name for i in run(WebCollectAgent(search, Reader()), req).sources[0].ingredients]
    assert "순대" in names


def test_ingredient_at_the_front_of_the_menu_name_stays() -> None:
    # 김치찌개 is made with 김치: the title still gives it (test 01 relies on this too).
    response = run(WebCollectAgent(provider([result(0)]), Reader()), request())
    assert response.sources[0].ingredients[0].canonical_name == "김치"


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


def test_10_22_llm_failure_keeps_results_naming_the_menu_with_dictionary_names() -> None:
    # Unread results fill only when the title names the menu (2026-11-08): 김치찜 goes.
    results = [result(0, title="김치찜 레시피"), result(1)]
    for reader in (FailingReader(), SlowReader(), None):
        agent = WebCollectAgent(provider(results), reader, llm_seconds=0.05)

        response = run(agent, request())

        assert [s.source_url for s in response.sources] == [results[1].url]
        assert codes(response.warnings) == ["web_llm_extraction_failed"]
        assert response.warnings[0].forces_caution is True


def test_13_deadline_mid_search_adds_warning() -> None:
    class HalfThenSlow(SearchProvider):
        def search(self, query, start, count, sites=()):
            if start == 1:
                return [result(i, title="김치찜 레시피" if i < count - 3 else "김치찌개") for i in range(count)]
            time.sleep(0.5)
            return []

    agent = WebCollectAgent(
        HalfThenSlow(), Reader(drop={"김치찜 레시피"}), total_seconds=0.2, llm_min_seconds=0
    )

    response = run(agent, request())

    assert len(response.sources) == 3
    assert codes(response.warnings) == ["web_search_deadline_exceeded"]


class RecordingProvider(StaticSearchProvider):
    def __init__(self, results: list[SearchResult]) -> None:
        super().__init__({QUERY: results})
        self.calls: list[tuple[int, int]] = []
        self.sites: list[tuple[str, ...]] = []

    def search(self, query, start, count, sites=()):
        self.calls.append((start, count))
        if sites not in self.sites:
            self.sites.append(sites)
        return super().search(query, start, count, sites)


def test_17_cache_reads_from_rank_one_and_skips_cached_urls() -> None:
    # 10/27 meeting: no stored rank. Read from 1, ask for twice 10 more than the cache
    # holds, and skip the cached URLs (§2-4).
    results = [result(i) for i in range(20)]
    search = RecordingProvider(results)

    response = run(
        WebCollectAgent(search, Reader()),
        request(cached=results[:10]),
    )

    assert [s.source_url for s in response.sources] == [r.url for r in results[10:20]]
    # 20 results for the 30 asked: the priority sites ran out, so the blog is asked for
    # the 10 candidates still short in the same round (2026-11-02).
    assert search.calls == [(1, 30), (1, 10)]


def test_cache_entries_off_the_ranking_still_fill_ten_new_sources() -> None:
    # A cached URL that no longer shows up only makes the first ask bigger.
    results = [result(i) for i in range(12)]
    search = RecordingProvider(results)

    response = run(
        WebCollectAgent(search, Reader()),
        request(cached=[result(99), results[0]]),
    )

    assert [s.source_url for s in response.sources] == [r.url for r in results[1:11]]


def test_cached_url_is_not_counted_again() -> None:
    results = [result(0), result(1)]

    response = run(
        WebCollectAgent(provider(results), Reader()), request(cached=[results[0]])
    )

    assert [s.source_url for s in response.sources] == [results[1].url]


def test_19_storeless_scan_reads_the_cache_and_returns_the_same() -> None:
    # Storeless scans skip saving and review in ⓪, not here (§2-4 "가게 없는 스캔").
    results = [result(i) for i in range(20)]
    with_store = request(cached=results[:10])
    storeless = with_store.model_copy(
        update={"context": with_store.context.model_copy(update={"store_id": None})}
    )

    a = run(WebCollectAgent(provider(results), Reader()), with_store)
    b = run(WebCollectAgent(provider(results), Reader()), storeless)

    assert b.context.store_id is None
    assert [s.source_url for s in b.sources] == [r.url for r in results[10:20]]
    assert b.model_dump(exclude={"context"}) == a.model_dump(exclude={"context"})


def test_21_recipe_without_the_menu_name_in_its_title_is_kept() -> None:
    # The LLM keeps what it is unsure about (§3-0); the title rule flags it in stage 2.
    results = [result(0, title="엄마표 얼큰 찌개"), result(1)]

    response = run(WebCollectAgent(provider(results), Reader()), request())

    assert [s.title for s in response.sources] == ["엄마표 얼큰 찌개", "김치찌개 황금레시피"]
    assert response.warnings == []


def test_18_search_failure_with_cache_is_not_an_error() -> None:
    response = run(
        WebCollectAgent(FailingProvider(), Reader()),
        request(cached=[result(0)]),
    )

    assert response.found is False
    assert response.errors == []


def test_no_search_key_is_told_apart_from_a_real_failure() -> None:
    # #120: without a search key nothing is searched. That gets its own code and is not
    # retryable; a real search failure stays web_search_timeout. Both end in no_information.
    no_key = run(WebCollectAgent(UnconfiguredSearchProvider(), Reader()), request())
    failed = run(WebCollectAgent(FailingProvider(), Reader()), request())

    assert no_key.found is False and failed.found is False
    assert [(e.code, e.retryable, e.fallback) for e in no_key.errors] == [
        ("web_search_not_configured", False, "no_information")
    ]
    assert [(e.code, e.retryable) for e in failed.errors] == [("web_search_timeout", True)]


def test_no_search_key_with_cache_judges_on_the_cache_and_says_why() -> None:
    response = run(
        WebCollectAgent(UnconfiguredSearchProvider(), Reader()),
        request(cached=[result(0)]),
    )

    assert response.errors == []
    assert [(w.code, w.forces_caution) for w in response.warnings] == [
        ("web_search_not_configured", False)
    ]


def test_read_recipes_come_first_and_unread_ones_only_fill_the_rest() -> None:
    class SecondCallFails(Reader):
        async def read(self, menu_name, results):
            if len(self.sizes) == 1:
                self.sizes.append(len(results))
                raise TimeoutError("llm slow")
            return await super().read(menu_name, results)

    results = [result(i) for i in range(15)]

    response = run(WebCollectAgent(provider(results), SecondCallFails()), request())

    # Results 3~5 went unread; twelve read recipes are enough, so they are left out.
    assert [s.source_url for s in response.sources] == [
        r.url for r in results[:3] + results[6:13]
    ]
    assert response.warnings == []


def test_priority_sites_keep_recipe_urls_only() -> None:
    # §2-3-1: profiles, counseling pages and other domains are dropped before reading.
    def at(url: str) -> SearchResult:
        return SearchResult(query=QUERY, title="김치찌개", url=url, content=RECIPE_TEXT)

    kept = [
        "https://www.10000recipe.com/recipe/6864674",
        "https://wtable.co.kr/recipes/zNcioPqkvQZ7q2TtHDFJeCDu",
        "https://semie.cooking/cooking/id/6219",
        "https://semie.cooking/recipe-lab/archive/dwaejigogikimchijjigae",
        "https://www.semie.cooking/semie/archive/tofunoodle-mushroom-japchae?code=r6-7",
    ]
    dropped = [
        "https://www.10000recipe.com/profile/recipe.html?uid=suwolhan",
        "https://wtable.co.kr/recipes",
        "https://wtable.co.kr/",
        "https://semie.cooking/counseling/worry/id/702",
        "https://semie.cooking/recipe-lab/list/recipe",
        "https://story.kakao.com/ch/10000recipe",
    ]

    class Mixed(SearchProvider):
        def search(self, query, start, count, sites=()):
            return [at(u) for u in kept + dropped][start - 1 : start - 1 + count] if sites else []

    reader = Reader()
    response = run(WebCollectAgent(Mixed(), reader), request())

    assert [s.source_url for s in response.sources] == kept


def test_wtable_and_semie_results_count_only_when_the_title_names_the_menu() -> None:
    # 2026-11-08: their site: search mostly returns other dishes (김치찜 for 갓김치), and
    # their titles are the dish name. 만개의레시피 titles are free text: the LLM judges them.
    def at(url: str, title: str) -> SearchResult:
        return SearchResult(query=QUERY, title=title, url=url, content=RECIPE_TEXT)

    results = [
        at("https://wtable.co.kr/recipes/a", "돼지고기 김치찜 - 우리의식탁"),
        at("https://wtable.co.kr/recipes/b", "[강레오] 김치 찌개"),
        at("https://semie.cooking/cooking/id/1", "김치쌀국수 - semie cooking"),
        at("https://www.10000recipe.com/recipe/1", "백종원 7분 찌개"),
    ]

    class Priority(SearchProvider):
        def search(self, query, start, count, sites=()):
            return results[start - 1 : start - 1 + count] if sites else []

    response = run(WebCollectAgent(Priority(), Reader()), request())

    assert [s.source_url for s in response.sources] == [
        "https://wtable.co.kr/recipes/b",
        "https://www.10000recipe.com/recipe/1",
    ]


def test_11_general_web_fills_what_the_priority_sites_lack() -> None:
    blog = [
        SearchResult(query=QUERY, title="김치찌개", url=f"https://blog.naver.com/a/{i}", content=RECIPE_TEXT)
        for i in range(10)
    ]
    search = RecordingProvider([result(i) for i in range(3)] + blog)

    response = run(WebCollectAgent(search, Reader()), request())

    urls = [s.source_url for s in response.sources]
    assert urls[:3] == [result(i).url for i in range(3)]
    assert urls[3:] == [b.url for b in blog[:7]]
    assert search.sites == [PRIORITY_DOMAINS, ()]


class SiteProvider(SearchProvider):
    """Priority-site and blog results kept apart, so a test can tell who gave what."""

    def __init__(self, priority: list[SearchResult], blog: list[SearchResult]) -> None:
        self.results = {PRIORITY_DOMAINS: priority, (): blog}
        self.calls: list[tuple[tuple[str, ...], int, int]] = []

    def search(self, query, start, count, sites=()):
        self.calls.append((tuple(sites), start, count))
        return self.results[tuple(sites)][start - 1 : start - 1 + count]


def blog_post(i: int) -> SearchResult:
    return SearchResult(query=QUERY, title="김치찌개", url=f"https://blog.naver.com/a/{i}", content=RECIPE_TEXT)


def test_12_enough_priority_recipes_keep_the_blog_out() -> None:
    # 10 priority candidates for the 20 a round reads: the blog fills the round, but
    # when all 10 priority recipes are valid, none of the blog posts is used.
    search = SiteProvider([result(i) for i in range(10)], [blog_post(i) for i in range(10)])
    reader = Reader()

    response = run(WebCollectAgent(search, reader), request())

    assert [s.source_url for s in response.sources] == [result(i).url for i in range(10)]
    assert [c[0] for c in search.calls] == [PRIORITY_DOMAINS, ()]
    assert sum(reader.sizes) == 20  # one round: pages and the LLM run once for both


def test_12_full_priority_round_does_not_search_the_blog() -> None:
    search = SiteProvider([result(i) for i in range(30)], [blog_post(i) for i in range(10)])

    run(WebCollectAgent(search, Reader()), request())

    assert [c[0] for c in search.calls] == [PRIORITY_DOMAINS]


def test_blog_fills_a_round_short_of_priority_recipe_urls() -> None:
    # A full priority page of 20 where only 6 are recipe URLs (a profile page, say): the
    # blog adds the 14 missing candidates in the same round instead of a second round.
    profiles = [
        SearchResult(query=QUERY, title="프로필", url=f"https://www.10000recipe.com/profile/{i}", content=RECIPE_TEXT)
        for i in range(14)
    ]
    search = SiteProvider([result(i) for i in range(6)] + profiles, [blog_post(i) for i in range(20)])
    reader = Reader(drop={"김치찌개"})  # every blog post is another dish here

    response = run(WebCollectAgent(search, reader), request())

    assert [c[:2] for c in search.calls[:2]] == [(PRIORITY_DOMAINS, 1), ((), 1)]
    assert search.calls[1][2] == 14
    assert [s.source_url for s in response.sources[:6]] == [result(i).url for i in range(6)]


def test_no_llm_call_when_too_little_time_is_left() -> None:
    # A call with less than LLM_MIN_SECONDS left nearly always times out and is still
    # paid for; the results are kept unread instead (§6-3).
    reader = Reader()

    response = run(WebCollectAgent(provider([result(0)]), reader, total_seconds=1.0), request())

    assert reader.sizes == []
    assert len(response.sources) == 1
    assert codes(response.warnings) == ["web_llm_extraction_failed"]


def test_page_bodies_are_read_for_kept_results_only() -> None:
    class Pages:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def fetch(self, result):
            self.urls.append(result.url)
            return replace(result, content=RECIPE_TEXT)

    profile = SearchResult(
        query=QUERY, title="김치찌개", url="https://www.10000recipe.com/profile/recipe.html?uid=a"
    )
    bare = [result(i, content="") for i in range(2)]
    pages = Pages()

    response = run(
        WebCollectAgent(provider([profile, *bare]), Reader(), pages=pages), request()
    )

    assert sorted(pages.urls) == sorted(r.url for r in bare)
    assert len(response.sources) == 2


def test_a_slow_page_keeps_search_text_and_the_others_are_used() -> None:
    class OneSlowPage:
        async def fetch(self, result):
            if result.url.endswith("/0"):
                await asyncio.sleep(0.5)
            return replace(result, content=RECIPE_TEXT)

    bare = [result(i, content="") for i in range(2)]

    response = run(
        WebCollectAgent(provider(bare), Reader(), pages=OneSlowPage(), page_seconds=0.1),
        request(),
    )

    names = [[i.canonical_name for i in s.ingredients] for s in response.sources]
    # The slow page has only its title ("김치찌개 황금레시피" gives 김치).
    assert names == [["김치"], ["김치", "돼지고기", "두부", "양파", "대파"]]


def test_unavailable_cache_is_not_read_as_no_cache() -> None:
    # The backend failed to load the cache: ⓪ sends no cached sources and a warning
    # (⓪ item_graph web_collect_node). ⑥ searches anew from rank 1 and keeps the warning (§3-2).
    req = WebCollectRequest.model_validate(
        {
            **request().model_dump(mode="json"),
            "warnings": [
                {
                    "code": "knowledge_slice_unavailable",
                    "node": "supervisor",
                    "item_id": "scan-123:0",
                    "message": "web_search_cache could not be loaded",
                    "forces_caution": True,
                }
            ],
        }
    )
    results = [result(i) for i in range(10)]
    search = RecordingProvider(results)

    response = run(WebCollectAgent(search, Reader()), req)

    assert response.found is True
    assert [s.source_url for s in response.sources] == [r.url for r in results]
    assert search.calls[0][0] == 1
    assert codes(response.warnings) == ["knowledge_slice_unavailable"]
    assert response.warnings[0].forces_caution is True
    assert response.errors == []


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
