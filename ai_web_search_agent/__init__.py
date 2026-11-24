"""⑥ Web Search Agent — web recipes for menus the DB does not know (docs/6-agent-websearch.md).

⑥ only collects and counts. It never tags ingredients (④ does) and never writes to any
DB (the backend does, from ⑦ commands).
"""

from .aggregate import WebAggregateAgent, aggregate_sources
from .collect import PageFetcher, RecipeReader, RecipeReading, WebCollectAgent
from .dictionary import find_ingredient_names, load_dictionary
from .menu_identity import dish_identity_suffix, web_search_menu_name, web_search_menu_names
from .pages import HttpPageFetcher
from .providers import (
    NaverSearchProvider,
    SearchNotConfiguredError,
    SearchProvider,
    SearchResult,
    StaticSearchProvider,
    UnconfiguredSearchProvider,
    search_provider_from_env,
)
from .reader import OpenAIRecipeReader

__all__ = [
    "HttpPageFetcher",
    "NaverSearchProvider",
    "OpenAIRecipeReader",
    "PageFetcher",
    "RecipeReader",
    "RecipeReading",
    "SearchNotConfiguredError",
    "SearchProvider",
    "SearchResult",
    "StaticSearchProvider",
    "UnconfiguredSearchProvider",
    "WebAggregateAgent",
    "WebCollectAgent",
    "aggregate_sources",
    "dish_identity_suffix",
    "find_ingredient_names",
    "load_dictionary",
    "search_provider_from_env",
    "web_search_menu_name",
    "web_search_menu_names",
]
