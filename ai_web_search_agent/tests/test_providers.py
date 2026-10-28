"""Search provider interface and the ⑥ "no DB writes" guarantee (#123)."""

import ast
from pathlib import Path

import httpx
import pytest

from ai_web_search_agent import NaverSearchProvider, SearchResult, StaticSearchProvider
from ai_web_search_agent.providers import NaverSearchConfigError

PACKAGE_DIR = Path(__file__).resolve().parents[1]
DB_MODULES = {"sqlite3", "psycopg", "psycopg2", "sqlalchemy", "asyncpg", "pymysql"}


def test_static_provider_returns_canned_results_up_to_top_k() -> None:
    results = [
        SearchResult(query="김치찌개 레시피", title=f"김치찌개 {i}", url=f"https://www.10000recipe.com/recipe/{i}")
        for i in range(5)
    ]
    provider = StaticSearchProvider({"김치찌개 레시피": results})
    assert provider.search("김치찌개 레시피", 1, 3) == results[:3]
    assert provider.search("김치찌개 레시피", 4, 3) == results[3:5]
    assert provider.search("김치찌개 레시피", 1, 3, sites=("wtable.co.kr",)) == []
    assert provider.search("없는 검색어", 1, 3) == []
    assert results[0].domain == "10000recipe.com"


def naver(handler) -> NaverSearchProvider:
    return NaverSearchProvider("id", "secret", client=httpx.Client(transport=httpx.MockTransport(handler)))


def items(prefix: str, n: int) -> dict:
    return {
        "items": [
            {"title": f"<b>김치찌개</b> {prefix}{i}", "link": f"https://{prefix}/{i}", "description": "&quot;재료&quot;"}
            for i in range(n)
        ]
    }


def test_naver_merges_priority_sites_in_turn() -> None:
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append((request.url.path, params))
        assert request.headers["X-NCP-APIGW-API-KEY-ID"] == "id"
        assert request.headers["X-NCP-APIGW-API-KEY"] == "secret"
        site = params["query"].split()[0].removeprefix("site:")
        return httpx.Response(200, json=items(site, {"a.com": 3, "b.com": 1}[site]))

    results = naver(handler).search("김치찌개 레시피", 2, 3, sites=("a.com", "b.com"))

    assert [r.url for r in results] == ["https://b.com/0", "https://a.com/1", "https://a.com/2"]
    assert {path for path, _ in seen} == {"/search/v1/webkr"}
    assert all(p["start"] == "1" and p["display"] == "4" for _, p in seen)
    assert results[0].title == "김치찌개 b.com0"
    assert results[0].snippet == '"재료"'


def test_naver_general_web_is_the_blog_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/v1/blog"
        assert dict(request.url.params) == {"query": "김치찌개 레시피", "start": "11", "display": "5"}
        return httpx.Response(200, json=items("blog.naver.com", 5))

    assert len(naver(handler).search("김치찌개 레시피", 11, 5)) == 5


def test_naver_errors_are_raised() -> None:
    provider = naver(lambda request: httpx.Response(429, json={"errorMessage": "limit"}))

    with pytest.raises(httpx.HTTPStatusError):
        provider.search("김치찌개 레시피", 1, 10)


def test_naver_from_env_needs_both_keys(monkeypatch) -> None:
    monkeypatch.delenv("NAVER_SEARCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_SEARCH_CLIENT_SECRET", raising=False)
    assert NaverSearchProvider.from_env_if_configured() is None

    monkeypatch.setenv("NAVER_SEARCH_CLIENT_ID", "id")
    with pytest.raises(NaverSearchConfigError):
        NaverSearchProvider.from_env_if_configured()

    monkeypatch.setenv("NAVER_SEARCH_CLIENT_SECRET", "secret")
    assert isinstance(NaverSearchProvider.from_env_if_configured(), NaverSearchProvider)


def test_web_search_package_imports_no_database_driver() -> None:
    """⑥ has no DB access at all; storage is the backend's job via ⑦ commands (⑥ §0)."""

    imported: set[str] = set()
    for path in PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
    assert not imported & DB_MODULES
    assert "ai_ruleengine" not in imported


def test_web_search_responses_have_no_storage_command() -> None:
    """Storage commands come from ⑦ via the Supervisor, never from ⑥ (⑥ §4 #1)."""

    from ai_supervisor.contracts.web_search import WebAggregateResponse, WebCollectResponse

    for model in (WebCollectResponse, WebAggregateResponse):
        assert not [name for name in model.model_fields if "command" in name]
