"""
한스푼 Rule Engine - 메뉴 DB 로더

ai_ruleengine/data/menus.csv 를 읽어 메뉴 딕셔너리 리스트로 반환.
"""

import csv
from pathlib import Path

_CSV_PATH = Path(__file__).parent / "data" / "menus.csv"


def load_menu_db() -> list[dict]:
    menus = []
    with open(_CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ambiguity_raw = row.get("애매함 태그", "").strip()
            ambiguity_flags = set()
            if ambiguity_raw:
                for flag in ambiguity_raw.split(","):
                    flag = flag.strip()
                    if flag:
                        ambiguity_flags.add(flag)

            recipe_raw = row.get("레시피", "").strip()
            ingredients = [i.strip() for i in recipe_raw.split(",") if i.strip()]

            menus.append({
                "category": row.get("카테고리", "").strip(),
                "name": row.get("메뉴명", "").strip(),
                "ingredients": ingredients,
                "source": row.get("레시피 출처", "").strip(),
                "ambiguity_flags": ambiguity_flags,
            })
    return menus


_CACHED_DB: list[dict] | None = None


def get_menu_db() -> list[dict]:
    global _CACHED_DB
    if _CACHED_DB is None:
        _CACHED_DB = load_menu_db()
    return _CACHED_DB
