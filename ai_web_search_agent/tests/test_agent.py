import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from agent import WebSearchAgent
from cache import WebSearchCache
from config import AgentConfig
from providers import StaticSearchProvider


def _rule_result(**overrides):
    data = {
        "menu_name_ko": "김치찌개",
        "risk_level": "safe",
        "hit_tags": [],
        "triggered_flags": ["has_unclear_jeotgal"],
        "forbidden_tags": ["is_shrimp"],
    }
    data.update(overrides)
    return data


def test_web_agent_uses_rule_tagger_and_saves_cache(tmp_path):
    provider = StaticSearchProvider(
        {
            "김치찌개 재료": [
                {
                    "title": "김치찌개 레시피",
                    "url": "https://www.10000recipe.com/recipe/1",
                    "snippet": "김치찌개 재료는 돼지고기, 두부, 새우젓, 멸치육수를 사용합니다.",
                }
            ]
        }
    )
    config = AgentConfig(db_path=tmp_path / "web.sqlite3", top_k=5, max_queries=1)
    agent = WebSearchAgent(provider=provider, config=config, cache=WebSearchCache(config.db_path, 30))

    result = agent.verify(_rule_result())

    assert result["search_status"] == "searched"
    assert "is_pork" in result["web_tags"]
    assert "is_shrimp" in result["web_tags"]
    assert result["web_forbidden_hits"] == ["is_shrimp"]

    cached = agent.verify(_rule_result())
    assert cached["search_status"] == "cache_hit"


def test_danger_is_never_downgraded(tmp_path):
    config = AgentConfig(db_path=tmp_path / "web.sqlite3", max_queries=1)
    agent = WebSearchAgent(
        provider=StaticSearchProvider(),
        config=config,
        cache=WebSearchCache(config.db_path, 30),
    )
    judged = {
        "scan_session": {},
        "menu_analyses": [_rule_result(risk_level="danger", hit_tags=["is_shrimp"])],
    }

    result = agent.verify_all(judged)

    assert result["menu_analyses"][0]["risk_level_after_web"] == "danger"


def test_agent_can_run_ruleengine_before_web_verification(tmp_path):
    config = AgentConfig(db_path=tmp_path / "web.sqlite3", max_queries=1)
    agent = WebSearchAgent(
        provider=StaticSearchProvider(),
        config=config,
        cache=WebSearchCache(config.db_path, 30),
    )
    ocr_result = {
        "scan_session": {"menu_count": 1},
        "menu_analyses": [{"menu_name_ko": "김치찌개", "is_spicy": False}],
    }
    profile = {"allergies": ["is_pork"]}

    result = agent.analyze_and_verify_all(ocr_result, profile)

    item = result["menu_analyses"][0]
    assert item["risk_level"] == "danger"
    assert item["hit_tags"] == ["is_pork"]
    assert "web_verification" in item
