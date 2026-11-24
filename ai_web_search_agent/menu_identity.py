"""Resolve a branded or regional menu label to its dish identity for web search.

The OCR/display name stays untouched.  This module only chooses the name sent to the
recipe search and reader: ``오봉이네백반`` should search for ``백반``, while an actual
ingredient variant such as ``치즈라면`` must keep the full name.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURATED_MENUS = ROOT / "ai_ruleengine" / "data" / "menus.csv"

# Common restaurant dishes that are useful web-search identities but are not yet in the
# curated menu seed.  Keep this small: suffix matching is allowed only when the prefix is
# independently recognised as a brand, region or preparation label below.
EXTRA_DISH_IDENTITIES = frozenset({"백반"})

# A location immediately before a known dish changes its style, not its food identity.
# Administrative endings cover labels such as 경주시/해운대구 without enumerating them.
REGION_NAMES = frozenset({
    "개성", "경주", "공주", "광주", "강릉", "남원", "대구", "대전", "목포",
    "부산", "부여", "서울", "속초", "수원", "안동", "여수", "의정부", "인천",
    "전주", "제주", "진주", "청주", "춘천", "충주", "통영", "평양", "함흥",
})
REGION_ENDINGS = ("특별자치도", "특별시", "광역시", "자치시", "자치구", "시", "군", "구", "동", "읍", "면", "리")

# These labels describe who/where made the dish.  They are deliberately narrower than
# arbitrary prefixes so 치즈라면·김치만두·해물파전 are never collapsed.
CRAFT_LABELS = ("수제", "손", "전통", "정통", "옛날")
BRAND_ENDINGS = ("이네", "식당", "음식점", "전문점", "명가", "할머니", "할매", "엄마", "네", "집", "표")
BRAND_PREFIXES = ("원조",)


def _compact(value: str) -> str:
    return "".join(value.split())


@lru_cache(maxsize=1)
def dish_identities() -> tuple[str, ...]:
    """Known dish names, longest first so 떡만두국 wins over 만두."""

    names = set(EXTRA_DISH_IDENTITIES)
    with CURATED_MENUS.open(encoding="utf-8-sig", newline="") as file:
        names.update(row["메뉴명"].strip() for row in csv.DictReader(file) if row["메뉴명"].strip())
    return tuple(sorted({_compact(name) for name in names}, key=lambda name: (-len(name), name)))


def _strip_craft_labels(prefix: str) -> tuple[str, bool]:
    changed = False
    previous = None
    while prefix and prefix != previous:
        previous = prefix
        for label in CRAFT_LABELS:
            if prefix.endswith(label):
                prefix = prefix[: -len(label)]
                changed = True
                break
    return prefix, changed


def _is_region_label(prefix: str) -> bool:
    if prefix.endswith("식"):
        prefix = prefix[:-1]
    return prefix in REGION_NAMES or (
        len(prefix) >= 2 and any(prefix.endswith(ending) for ending in REGION_ENDINGS)
    )


def _is_brand_label(prefix: str) -> bool:
    return (
        any(prefix.startswith(label) for label in BRAND_PREFIXES)
        or any(prefix.endswith(label) for label in BRAND_ENDINGS)
    )


def web_search_menu_names(menu_name: str) -> tuple[str, ...]:
    """Search targets in priority order without changing the API menu name.

    A regional style can change a recipe, so ``개성식손만두`` is searched first and
    ``만두`` fills a shortage.  A store label does not describe the recipe, so
    ``오봉이네백반`` searches only ``백반``.  Ingredient variants such as 김치만두 are
    not recognised as either prefix and therefore keep their full name.
    """

    compact = _compact(menu_name)
    for dish in dish_identities():
        if compact == dish or not compact.endswith(dish):
            continue
        prefix = compact[: -len(dish)]
        remaining, had_craft = _strip_craft_labels(prefix)
        if _is_region_label(remaining):
            return compact, dish
        if not remaining and had_craft:
            return (dish,)
        if _is_brand_label(remaining):
            return (dish,)
    return (compact,)


def web_search_menu_name(menu_name: str) -> str:
    """The first web-search target (kept for callers that need one display name)."""

    return web_search_menu_names(menu_name)[0]


def dish_identity_suffix(menu_name: str) -> str:
    """The longest known dish that is the whole label or its suffix.

    Unlike :func:`web_search_menu_name`, this also recognises ingredient variants such
    as 김치만두 → 만두.  It is used only to stop the completed dish name from becoming
    one of its own web-extracted ingredients; it never changes the search query.
    """

    compact = _compact(menu_name)
    return next((dish for dish in dish_identities() if compact.endswith(dish)), compact)
