"""Ingredient names in the user's language from the team's translation tables (docs/8-agent-xai.md §4).

Two tables, both in ``crawling/``:

- ``ingredient_translations.csv``: the 324 standard ingredients. The backend loads it
  into ``ingredients.name_i18n`` and sends it as ``localized_names``.
- ``ingredient_translations_extra.csv``: names outside the standard list, from the
  crawled recipes (2026-11-02). They never get an ``ingredient_id``, so a web ingredient
  ④ could not map (``ingredient_id: null``) is looked up here by its Korean name. Only
  rows of ``kind: ingredient`` are used.

``localized_names`` from the request always wins; the tables are the fallback, and a
name in neither stays Korean.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from ai_supervisor.contracts.common import IngredientRef

CRAWLING = Path(__file__).resolve().parent.parent / "crawling"
STANDARD_PATH = CRAWLING / "ingredient_translations.csv"
EXTRA_PATH = CRAWLING / "ingredient_translations_extra.csv"
LOCALES = ("en", "ar", "zh-CN", "zh-TW", "ja", "es")


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def standard_rows() -> list[dict[str, str]]:
    return _read(STANDARD_PATH)


@lru_cache(maxsize=1)
def all_rows() -> list[dict[str, str]]:
    """Standard rows, then the extra ingredients, each as ``name_ko`` + six languages."""

    extra = [
        {key: row[key] for key in ("name_ko", *LOCALES)}
        for row in _read(EXTRA_PATH)
        if row["kind"] == "ingredient"
    ]
    return [*standard_rows(), *extra]


@lru_cache(maxsize=1)
def _by_korean() -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, str]] = {}
    for row in all_rows():
        table.setdefault(row["name_ko"], row)
    return table


def dictionary_name(korean_name: str, locale: str) -> str | None:
    """``korean_name`` in ``locale`` from the tables, or ``None`` when it is not there."""

    if locale == "ko":
        return korean_name
    value = _by_korean().get(korean_name, {}).get(locale, "").strip()
    return value or None


def display_name(ingredient: IngredientRef, localized: dict[int | None, str], locale: str) -> str:
    """The name shown to the user: ``localized_names`` first, then the tables, else Korean."""

    if ingredient.ingredient_id is not None and ingredient.ingredient_id in localized:
        return localized[ingredient.ingredient_id]
    return dictionary_name(ingredient.canonical_name, locale) or ingredient.canonical_name
