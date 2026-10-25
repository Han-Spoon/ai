"""⑥ Web Search Agent — web recipes for menus the DB does not know (docs/6-agent-websearch.md).

⑥ only collects and counts. It never tags ingredients (④ does) and never writes to any
DB (the backend does, from ⑦ commands).
"""

from .aggregate import WebAggregateAgent, aggregate_sources
from .collect import RecipeReader, RecipeReading, WebCollectAgent
from .dictionary import find_ingredient_names, load_dictionary
from .providers import SearchProvider, SearchResult, StaticSearchProvider

__all__ = [
    "RecipeReader",
    "RecipeReading",
    "SearchProvider",
    "SearchResult",
    "StaticSearchProvider",
    "WebAggregateAgent",
    "WebCollectAgent",
    "aggregate_sources",
    "find_ingredient_names",
    "load_dictionary",
]
