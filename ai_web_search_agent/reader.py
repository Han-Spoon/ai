"""⑥ stage 1 LLM: which search results are this menu's recipes, and their ingredients
(docs/6-agent-websearch.md §2-1 step 2, §3-0).

One call per group of results (``collect.py`` sends three at a time, side by side). Code
still does the rest: ``collect.py`` keeps every result
the LLM is unsure about, unions LLM names with the dictionary, and drops LLM names the
text does not contain. Same client shape as ② ``ai_normalization/openai_resolver.py``;
the SDK reads ``OPENAI_BASE_URL`` itself, so a gateway such as BAZE needs no code here.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from pydantic import BaseModel, Field

from .collect import LLM_SECONDS, RecipeReading
from .providers import SearchResult

DEFAULT_WEB_SEARCH_OPENAI_MODEL = "gpt-5.4-mini"
MAX_TEXT_CHARS = 2000
MAX_OUTPUT_TOKENS = 2000

INSTRUCTIONS = """You read Korean web search results for one restaurant menu.
For every result, by its index:
- is_menu_recipe: false only for restaurant reviews, ads, shopping pages, or a recipe
  that is clearly a different dish. A title naming another dish is a different dish
  even when ingredients overlap (for 김치찌개: 김치찜, 김치볶음밥, 애호박찌개, 김치쌀국수;
  for 닭한마리: 닭가슴살 샐러드, 닭볶음탕). The same dish with an added or swapped
  ingredient is the same dish (for 김치찌개: 참치김치찌개, 돼지고기 김치찌개). If unsure, true.
- ingredients: ingredient names exactly as written in that result's text, in Korean.
  Do not translate, normalize, or add ingredients that are not written there.
Return one entry for every index you were given."""


class WebSearchOpenAIConfigError(ValueError):
    """The optional ⑥ OpenAI environment is invalid."""


class _Reading(BaseModel):
    index: int
    is_menu_recipe: bool
    ingredients: list[str] = Field(default_factory=list)


class _Readings(BaseModel):
    readings: list[_Reading]


class OpenAIRecipeReader:
    """``RecipeReader`` backed by the OpenAI Responses API. Raises on any bad answer;
    ``WebCollectAgent`` then keeps every result and adds ``web_llm_extraction_failed``."""

    def __init__(self, client: Any, *, model: str = DEFAULT_WEB_SEARCH_OPENAI_MODEL) -> None:
        self.client = client
        self.model = model

    @classmethod
    def from_env_if_configured(
        cls, *, client_factory: Callable[..., Any] | None = None
    ) -> "OpenAIRecipeReader | None":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        model = os.getenv("WEB_SEARCH_OPENAI_MODEL", DEFAULT_WEB_SEARCH_OPENAI_MODEL).strip()
        if not model:
            raise WebSearchOpenAIConfigError("WEB_SEARCH_OPENAI_MODEL must not be empty")
        raw = os.getenv("WEB_SEARCH_OPENAI_TIMEOUT_SECONDS")
        try:
            timeout = LLM_SECONDS if raw is None else float(raw)
        except ValueError as error:
            raise WebSearchOpenAIConfigError(
                "WEB_SEARCH_OPENAI_TIMEOUT_SECONDS must be a number"
            ) from error
        factory = client_factory or _async_openai_client
        # No retry: the whole web search has 7 seconds (§2-3).
        return cls(factory(api_key=api_key, timeout=timeout, max_retries=0), model=model)

    async def read(self, menu_name: str, results: list[SearchResult]) -> list[RecipeReading]:
        payload = {
            "menu": menu_name,
            "results": [
                {"index": i, "title": r.title, "text": r.text()[:MAX_TEXT_CHARS]}
                for i, r in enumerate(results)
            ],
        }
        response = await self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "developer", "content": INSTRUCTIONS},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            text_format=_Readings,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ValueError("OpenAI returned no parsed recipe readings")
        if not isinstance(parsed, _Readings):
            parsed = _Readings.model_validate(parsed)
        by_index = {r.index: r for r in parsed.readings}
        if len(parsed.readings) != len(results) or set(by_index) != set(range(len(results))):
            raise ValueError("recipe readings do not cover every result exactly once")
        return [
            RecipeReading(
                is_menu_recipe=by_index[i].is_menu_recipe,
                ingredients=[name.strip() for name in by_index[i].ingredients if name.strip()],
            )
            for i in range(len(results))
        ]


def _async_openai_client(**kwargs: Any) -> Any:
    try:
        from openai import AsyncOpenAI
    except ImportError as error:  # pragma: no cover - deployment dependency check
        raise WebSearchOpenAIConfigError(
            "openai package is required when OPENAI_API_KEY is configured"
        ) from error
    return AsyncOpenAI(**kwargs)
