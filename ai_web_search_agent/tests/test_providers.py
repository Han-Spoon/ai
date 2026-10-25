"""Search provider interface and the ⑥ "no DB writes" guarantee (#123)."""

import ast
from pathlib import Path

from ai_web_search_agent import SearchResult, StaticSearchProvider

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
