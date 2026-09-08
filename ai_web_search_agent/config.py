"""
Web Search Agent 설정.

가중치와 캐시 정책은 운영 중 조정하기 쉽도록 코드 경로를 분리했다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = AGENT_DIR / "data" / "web_search_cache.sqlite3"


SOURCE_WEIGHTS = {
    "official": 1.0,
    "recipe": 0.8,
    "blog": 0.55,
    "community": 0.35,
    "unknown": 0.25,
}

OFFICIAL_DOMAINS = {
    "mafra.go.kr",
    "korea.kr",
    "hansik.or.kr",
}

RECIPE_DOMAINS = {
    "10000recipe.com",
    "wtable.co.kr",
    "cooking.naver.com",
    "ourhome.co.kr",
}

COMMUNITY_DOMAINS = {
    "dcinside.com",
    "theqoo.net",
    "instiz.net",
    "ruliweb.com",
    "reddit.com",
}


@dataclass(frozen=True)
class AgentConfig:
    provider_name: str = field(default_factory=lambda: os.getenv("WEB_SEARCH_PROVIDER", "tavily"))
    tavily_api_key: str | None = field(default_factory=lambda: os.getenv("TAVILY_API_KEY"))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("WEB_SEARCH_DB_PATH", DEFAULT_DB_PATH)))
    cache_ttl_days: int = field(default_factory=lambda: int(os.getenv("WEB_SEARCH_CACHE_TTL_DAYS", "30")))
    top_k: int = field(default_factory=lambda: int(os.getenv("WEB_SEARCH_TOP_K", "5")))
    max_queries: int = field(default_factory=lambda: int(os.getenv("WEB_SEARCH_MAX_QUERIES", "8")))
    max_search_depth: int = field(default_factory=lambda: min(int(os.getenv("WEB_SEARCH_MAX_DEPTH", "1")), 1))
