"""
한스푼 Rule Engine - 베이스 메뉴 매칭 (AI2 Step 3)

Longest-match 전략으로 DB에서 베이스 메뉴를 탐색한다.
매칭 후 남은 토큰(remain)은 ingredient_tagger 에서 변형 재료로 처리된다.
"""

from menu_db import get_menu_db


def find_base_menu(stripped_name: str) -> tuple[dict | None, list[str]]:
    """
    Returns:
        (menu_row, remain_tokens)

    menu_row: DB 에서 찾은 메뉴 딕셔너리, 없으면 None.
    remain_tokens: 베이스 메뉴명 제거 후 남은 공백-분리 토큰 리스트.
                   menu_row 가 None 이면 [stripped_name].
    """
    if not stripped_name:
        return None, []

    db = get_menu_db()
    best_row: dict | None = None
    best_len = 0

    for row in db:
        menu_name = row["name"]
        if menu_name and menu_name in stripped_name:
            if len(menu_name) > best_len:
                best_row = row
                best_len = len(menu_name)

    if best_row is None:
        return None, [stripped_name]

    remain_raw = stripped_name.replace(best_row["name"], "").strip()
    remain_tokens = [t for t in remain_raw.split() if t]
    return best_row, remain_tokens
