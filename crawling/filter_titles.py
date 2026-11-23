# -*- coding: utf-8 -*-
"""
크롤링 원본에서 제목이 메뉴와 맞지 않는 레시피를 빼는 스크립트.

검색이 느슨한 사이트는 다른 요리를 함께 돌려준다. 2026-11-22에 추가한 22개 메뉴를
우리의식탁에서 검색하니 "닭한마리" 1,186개 중 제목이 맞는 것은 1개였다(닭가슴살 김밥,
파스타 등). 공통 관계 분류기로 동일 메뉴(`exact`)만 남기며, 명시한 별칭은
허용하지만 변형·다른 음식·애매한 제목은 기본 메뉴 자료에서 제외한다.

실행:
    python crawling/filter_titles.py <원본 CSV> --out <걸러낸 CSV>
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

try:  # package import / direct script import both work
    from .recipe_relation import classify_recipe_title
except ImportError:  # pragma: no cover
    from recipe_relation import classify_recipe_title

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
    # 기존 76개 메뉴를 300개로 늘리며 더함 (2026-11-22)
    "갈비구이": (("갈비", "구이"), "LA갈비"),
    "후라이드치킨": ("후라이드", "프라이드치킨", "닭튀김"),
    "배추김치": (("배추", "김치"), "김장김치", "포기김치", ("배추", "김장")),
    "총각김치": ("총각김치", ("총각무", "김치"), "알타리"),
    "깍두기": ("깍두기", "깍뚜기", "깍뚝이"),
    "오이소박이": (("오이", "소박이"),),
    "열무김치": ("열무김치", "열무물김치"),
    "무생채": ("무생채", "무채무침"),
    "계란말이": ("계란말이", "달걀말이"),
    "떡만두국": ("떡만두국", "떡만둣국", ("떡", "만두")),
    "육개장": ("육개장", "육계장"),
    "물냉면": ("물냉면", "물비빔냉면", "평양냉면", ("동치미", "냉면")),
    "비빔냉면": (("비빔", "냉면"),),
    "시금치나물": (("시금치", "나물"), ("시금치", "무침"), ("시금치", "무치")),
    "생선전": ("생선전", "동태전", "생선지짐"),
    "고기전": ("고기전", "육전"),
    "동그랑땡": ("동그랑땡", "동그랭땡", "돈저냐"),
    "라볶이": ("라볶이", "라뽁이", "라뽂이", "라볶기"),
    "떡꼬치": ("떡꼬치", "떡꼬지", ("떡볶이", "꼬치")),
    "간장게장": (("간장", "게장"),),
    "양념게장": (("양념", "게장"),),
    "깻잎장아찌": (("깻잎", "장아찌"), ("깻잎", "짱아찌"), ("깻잎", "짱아치"), "꺳잎장아찌"),
    "곱창전골": (("곱창", "전골"),),
    "전복죽": (("전복", "죽"),),
    "김치찌개": ("김치찌개", "김치찌게"),
    "된장찌개": ("된장찌개", "된장찌게"),
    "순두부찌개": ("순두부찌개", "순두부찌게"),
    "부대찌개": ("부대찌개", "부대찌게"),
    "갈비찜": ("갈비찜", "찜갈비"),
    "슈크림 붕어빵": (("슈크림", "붕어빵"), ("커스터드", "붕어빵"), ("크림", "붕어빵")),
    "감자전": ("감자전", "감자부침개"),
    "애호박전": ("애호박전", "애호박부침개"),
}

# 김치로 만든 다른 요리 ("갓김치볶음밥", "총각김치지짐", "열무김치국수" 등). 만개의레시피에서
# 김치 메뉴를 300위까지 읽으면 갓김치는 194개 중 103개가 이런 요리였다 (2026-11-22).
KIMCHI_DISH = ("볶음", "볶아", "찌개", "지짐", "찜", "부침", "김치전", "부추전", "국수", "김밥", "떡볶이", "말이")
KIMCHI_MENUS = ("배추김치", "총각김치", "깍두기", "갓김치", "나박김치", "백김치", "부추김치", "열무김치", "파김치", "오이소박이")

# 키워드가 있어도 다른 요리인 경우 (메뉴명 → 제목에 있으면 빼는 말)
TITLE_EXCLUDE: dict[str, tuple[str, ...]] = {
    "밀면": ("메밀",),  # 메밀면 ⊃ 밀면
    "막창구이": ("전골",),
    "간장게장": ("양념게장",),
    "양념게장": ("간장게장",),
    **{menu: KIMCHI_DISH for menu in KIMCHI_MENUS},
}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()  # "la갈비"도 "LA갈비"로 본다


def keeps(menu: str, title: str, known_menus: set[str] | None = None) -> bool:
    """동일 메뉴 또는 허용한 별칭의 레시피만 남긴다.

    메뉴명이 제목 어딘가에 있다는 이유만으로 파생 메뉴를 통과시키던 이전 규칙과
    달리, 공통 관계 분류기의 ``exact``만 허용한다.
    """

    relation = classify_recipe_title(menu, title, known_menus or {menu})
    return relation.included_in_base_prior


def main() -> None:
    parser = argparse.ArgumentParser(description="제목이 메뉴와 맞는 레시피만 남긴다.")
    parser.add_argument("input_csv")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.input_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    known_menus = {r["menu_query"] for r in rows}
    kept = [r for r in rows if keeps(r["menu_query"], r["recipe_title"], known_menus)]
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
