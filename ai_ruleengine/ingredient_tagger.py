"""
한스푼 Rule Engine - 재료 태깅 (AI2 Step 4–5)

Step 4: 베이스 메뉴 레시피의 명시 재료 태깅
Step 5: remain 토큰(변형 재료) 추가 태깅
"""

from constants import GROUP_VARIANTS, VARIANT_INGREDIENTS

# 각 태그별로 긴 키워드부터 매칭해야 false-positive 방지
_SORTED_VARIANTS: dict[str, list[str]] = {
    tag: sorted(keywords, key=len, reverse=True)
    for tag, keywords in VARIANT_INGREDIENTS.items()
}


def _tag_text(text: str, tags: set[str]) -> None:
    """단일 문자열을 검사해 매칭된 태그를 tags 에 추가 (in-place)."""
    for tag, keywords in _SORTED_VARIANTS.items():
        for kw in keywords:
            if kw in text:
                tags.add(tag)
                break


def tag_explicit(menu_row: dict) -> set[str]:
    """베이스 메뉴 명시 재료만 → 태그 set (Step 4)."""
    tags: set[str] = set()
    for ingredient in menu_row.get("ingredients", []):
        _tag_text(ingredient, tags)
    return tags


def tag_variants(remain_tokens: list[str]) -> set[str]:
    """remain 토큰만 → 태그 set (Step 5)."""
    tags: set[str] = set()
    for token in remain_tokens:
        for group_key, group_tags in GROUP_VARIANTS.items():
            if group_key in token:
                tags.update(group_tags)
        _tag_text(token, tags)
    return tags


def tag_ingredients(menu_row: dict, remain_tokens: list[str]) -> set[str]:
    """
    Returns 재료 태그 set.

    Step 4: base menu 의 ingredients 를 순회하며 태깅.
    Step 5: remain_tokens 을 순회하며 GROUP_VARIANTS 우선 → VARIANT_INGREDIENTS 순서로 태깅.
    """
    return tag_explicit(menu_row) | tag_variants(remain_tokens)


def has_unknown_remain(remain_tokens: list[str]) -> bool:
    """
    remain_tokens 중 VARIANT_INGREDIENTS / GROUP_VARIANTS 어느 것도 매칭 안 되는
    미확인 토큰이 있으면 True → GPT escalation 필요.
    """
    for token in remain_tokens:
        recognized = False
        for group_key in GROUP_VARIANTS:
            if group_key in token:
                recognized = True
                break
        if not recognized:
            for keywords in _SORTED_VARIANTS.values():
                for kw in keywords:
                    if kw in token:
                        recognized = True
                        break
                if recognized:
                    break
        if not recognized:
            return True
    return False
