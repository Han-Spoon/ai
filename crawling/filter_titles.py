# -*- coding: utf-8 -*-
"""
크롤링 원본에서 제목이 메뉴와 맞지 않는 레시피를 빼는 스크립트.

검색이 느슨한 사이트는 다른 요리를 함께 돌려준다. 2026-11-22에 추가한 22개 메뉴를
우리의식탁에서 검색하니 "닭한마리" 1,186개 중 제목이 맞는 것은 1개였다(닭가슴살 김밥,
파스타 등). 그래서 레시피 제목(공백 제거)에 메뉴의 키워드가 하나라도 있어야 남긴다.
`TITLE_KEYWORDS`에 없는 메뉴는 메뉴명 자체를 키워드로 쓴다.

실행:
    python crawling/filter_titles.py <원본 CSV> --out <걸러낸 CSV>
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

# 메뉴명 → 제목에 하나라도 있어야 하는 키워드 (표기 차이·같은 요리의 다른 이름).
# 튜플 키워드는 그 말이 모두 있어야 한다: ("낙지", "볶")은 "낙지 간장볶음"도 남긴다.
Keyword = str | tuple[str, ...]
TITLE_KEYWORDS: dict[str, tuple[Keyword, ...]] = {
    "순대국밥": ("순대국", "순댓국"),
    "소고기국밥": (("소고기", "국밥"), ("쇠고기", "국밥")),
    "뼈해장국": (("뼈", "해장국"),),  # "뼈(돼지등뼈)해장국"도 남긴다
    "제육볶음": ("제육",),
    "조개탕": ("조개탕", "바지락탕", "백합탕"),
    "돼지갈비": ("돼지갈비", ("돼지", "양념갈비")),
    "낙지볶음": (("낙지", "볶"), ("낚지", "볶")),
    "쭈꾸미볶음": (("쭈꾸미", "볶"), ("주꾸미", "볶")),
    "계란찜": ("계란찜", "달걀찜"),
    "곱창구이": (("곱창", "구이"),),
    "막창구이": ("막창",),
    "닭볶음탕": ("닭볶음탕", "닭도리탕"),
    "콩국수": ("콩국수", "콩국"),
}

# 키워드가 있어도 다른 요리인 경우 (메뉴명 → 제목에 있으면 빼는 말)
TITLE_EXCLUDE: dict[str, tuple[str, ...]] = {
    "밀면": ("메밀",),  # 메밀면 ⊃ 밀면
    "막창구이": ("전골",),
}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def keeps(menu: str, title: str) -> bool:
    t = compact(title)
    if any(word in t for word in TITLE_EXCLUDE.get(menu, ())):
        return False
    for keyword in TITLE_KEYWORDS.get(menu, (menu,)):
        words = (keyword,) if isinstance(keyword, str) else keyword
        if all(compact(word) in t for word in words):
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="제목이 메뉴와 맞는 레시피만 남긴다.")
    parser.add_argument("input_csv")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.input_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    kept = [r for r in rows if keeps(r["menu_query"], r["recipe_title"])]
    before = Counter((r["menu_query"], r["recipe_url"]) for r in rows)
    after = Counter((r["menu_query"], r["recipe_url"]) for r in kept)
    for menu in dict.fromkeys(r["menu_query"] for r in rows):
        n_before = sum(1 for m, _ in before if m == menu)
        n_after = sum(1 for m, _ in after if m == menu)
        print(f"{menu}: 레시피 {n_before} → {n_after}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(kept)
    print(f"행 {len(rows)} → {len(kept)}: {args.out}")


if __name__ == "__main__":
    main()
