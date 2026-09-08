from __future__ import annotations

import re
import sys
from pathlib import Path

from models import IngredientEvidence, SearchResult, SourceEvidence
from source_classifier import classify_source

BASE_DIR = Path(__file__).resolve().parents[1]
RULE_DIR = BASE_DIR / "ai_ruleengine"
if str(RULE_DIR) not in sys.path:
    sys.path.insert(0, str(RULE_DIR))

from constants import VARIANT_INGREDIENTS  # noqa: E402
from ingredient_tagger import tag_explicit  # noqa: E402


_RECIPE_KEYWORDS = ("재료", "레시피", "만드는 법", "만들기", "ingredients", "recipe", "육수", "양념")


def is_relevant_recipe_result(menu_name: str, result: SearchResult) -> bool:
    text = result.text().lower()
    compact_menu = menu_name.replace(" ", "").lower()
    compact_text = text.replace(" ", "")
    has_menu = compact_menu in compact_text
    has_recipe_signal = any(keyword.lower() in text for keyword in _RECIPE_KEYWORDS)
    return has_menu and has_recipe_signal


def extract_ingredient_evidence(menu_name: str, results: list[SearchResult]) -> dict[str, IngredientEvidence]:
    evidence: dict[str, IngredientEvidence] = {}
    seen_urls: set[str] = set()

    for result in results:
        if not result.url or result.url in seen_urls:
            continue
        seen_urls.add(result.url)
        if not is_relevant_recipe_result(menu_name, result):
            continue

        source_type, source_weight = classify_source(result.domain, result.title)
        source = SourceEvidence(
            query=result.query,
            title=result.title,
            url=result.url,
            domain=result.domain,
            source_type=source_type,
            source_weight=source_weight,
            fetched_at=result.fetched_at,
        )

        for ingredient in _explicit_variant_mentions(result.text()):
            tags = tag_explicit({"ingredients": [ingredient]})
            if not tags:
                continue
            row = evidence.setdefault(ingredient, IngredientEvidence(name=ingredient))
            row.tags.update(tags)
            row.sources.append(source)

    return evidence


def _explicit_variant_mentions(text: str) -> list[str]:
    mentions = []
    normalized_text = re.sub(r"\s+", "", text)
    for keywords in VARIANT_INGREDIENTS.values():
        for keyword in keywords:
            if keyword and keyword in normalized_text:
                mentions.append(keyword)
    return sorted(set(mentions), key=lambda value: (-len(value), value))

