"""
한스푼 Rule Engine - 수식어 제거 (AI2 Modifier Stripping v2)

Step 2: 메뉴 identity와 무관한 수식어를 제거하고,
        매운맛 키워드를 감지해 is_spicy 플래그를 반환한다.
"""

from constants import REMOVE_TOKENS, SPICY_TOKENS


def strip_modifiers(menu_name: str) -> tuple[str, bool]:
    """
    Returns:
        (stripped_name, is_spicy)

    is_spicy: SPICY_TOKENS 중 하나라도 포함되어 있으면 True.
    stripped_name: 매운맛 토큰 + REMOVE_TOKENS 제거 후 정규화된 이름.
    """
    name = menu_name.strip()
    is_spicy = False

    for token in SPICY_TOKENS:
        if token in name:
            is_spicy = True
            name = name.replace(token, " ")

    for token in REMOVE_TOKENS:
        if token in name:
            name = name.replace(token, " ")

    name = " ".join(name.split()).strip()
    return name, is_spicy
