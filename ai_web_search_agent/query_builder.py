from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
RULE_DIR = BASE_DIR / "ai_ruleengine"
if str(RULE_DIR) not in sys.path:
    sys.path.insert(0, str(RULE_DIR))

from menu_matcher import find_base_menu  # noqa: E402
from modifier_strip import strip_modifiers  # noqa: E402


_ENGLISH_QUERY_BY_MENU = {
    "김치찌개": "kimchi jjigae ingredients",
    "된장찌개": "doenjang jjigae ingredients",
    "순두부찌개": "sundubu jjigae ingredients",
    "비빔밥": "bibimbap ingredients",
    "김밥": "gimbap ingredients",
    "탄탄멘": "tantanmen ingredients",
    "라멘": "ramen ingredients",
}

_FLAG_QUERY_SUFFIXES = {
    "has_unclear_broth": ["육수 재료"],
    "has_unclear_seasoning": ["양념 재료"],
    "has_unclear_jeotgal": ["젓갈 액젓 새우젓"],
    "has_hidden_animal": ["육수 고기 동물성 재료"],
    "has_variant": ["종류", "레시피 변형"],
}


def normalize_menu_name(menu_name: str) -> str:
    stripped_name, _ = strip_modifiers(menu_name or "")
    return " ".join(stripped_name.split()).strip()


def build_queries(rule_result: dict, max_queries: int = 8) -> tuple[str, list[str]]:
    original = rule_result.get("menu_name_ko") or ""
    normalized = normalize_menu_name(original)
    base_menu, remain_tokens = find_base_menu(normalized)

    names = [normalized]
    if base_menu and base_menu.get("name") not in names:
        names.append(base_menu["name"])

    queries: list[str] = []
    for name in names:
        queries.extend([f"{name} 재료", f"{name} 레시피"])
        english = _ENGLISH_QUERY_BY_MENU.get(name)
        if english:
            queries.append(english)

    for token in remain_tokens:
        if token and base_menu:
            queries.append(f"{base_menu['name']} {token} 재료")

    for flag in rule_result.get("triggered_flags", []):
        for suffix in _FLAG_QUERY_SUFFIXES.get(flag, []):
            queries.append(f"{normalized} {suffix}")

    return normalized, _unique(queries)[:max_queries]


def _unique(items: list[str]) -> list[str]:
    seen = set()
    unique_items = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            unique_items.append(item)
    return unique_items

