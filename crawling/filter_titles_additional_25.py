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
    "생선전": ("생선전", "동태전"),
    "고기전": ("고기전", "육전"),
    "동그랑땡": ("동그랑땡", "돈저냐"),
    # 추가 크롤링 25개 메뉴 (menu_queries_additional_25.txt)
    "고등어구이": (("고등어", "구이"), "고갈비"),
    "오징어볶음": (("오징어", "볶"),),
    "고등어조림": (("고등어", "조림"),),
    "갈치조림": (("갈치", "조림"),),
    "두부조림": (("두부", "조림"),),
    "청국장찌개": ("청국장찌개", "청국장"),
    "동태찌개": ("동태찌개", "동태탕", "동태매운탕"),
    "꽃게탕": (("꽃게", "탕"),),
    "아귀찜": ("아귀찜", "아구찜"),
    "해물찜": ("해물찜", ("해물", "찜")),
    "해물파전": ("해물파전", ("해물", "파전")),
    "꼬막무침": (("꼬막", "무침"),),
    "도토리묵무침": (("도토리묵", "무침"),),
    "황태해장국": ("황태해장국", "황태국", "황태콩나물국"),
    "선지해장국": (("선지", "해장국"), ("선짓", "해장국")),
    "멸치볶음": (("멸치", "볶"),),
    "어묵볶음": (("어묵", "볶"), ("오뎅", "볶")),
    "진미채볶음": (("진미채", "볶"), ("오징어채", "볶")),
    "감자채볶음": (("감자채", "볶"), "감자볶음"),
    "소시지야채볶음": (
        ("소시지", "야채", "볶"),
        ("소세지", "야채", "볶"),
        "소야볶음",
        "쏘야볶음",
    ),
    "골뱅이무침": (("골뱅이", "무침"),),
    "닭발": ("닭발",),
    "닭똥집볶음": (("닭똥집", "볶"), ("닭근위", "볶")),
    "코다리조림": (("코다리", "조림"),),
    "북엇국": ("북엇국", "북어국"),
}

# 김치로 만든 다른 요리 ("갓김치볶음밥", "총각김치지짐", "열무김치국수" 등). 만개의레시피에서
# 김치 메뉴를 300위까지 읽으면 갓김치는 194개 중 103개가 이런 요리였다 (2026-11-22).
KIMCHI_DISH = ("볶음", "볶아", "찌개", "지짐", "찜", "부침", "김치전", "부추전", "국수", "김밥", "떡볶이", "말이")
KIMCHI_MENUS = ("배추김치", "총각김치", "깍두기", "갓김치", "나박김치", "백김치", "부추김치", "열무김치", "파김치", "오이소박이")

# 키워드가 있어도 다른 요리인 경우 (메뉴명 → 제목에 있으면 빼는 말)
TITLE_EXCLUDE: dict[str, tuple[Keyword, ...]] = {
    "밀면": ("메밀",),  # 메밀면 ⊃ 밀면
    "막창구이": ("전골",),
    "고등어구이": ("조림",),
    "고등어조림": ("구이",),
    "갈치조림": ("구이",),
    "오징어볶음": ("볶음밥", "볶음칼국수"),
    "두부조림": ("두부김치", "두부강정"),
    "청국장찌개": ("비빔", "볶음", "쌈"),
    "꽃게탕": ("꽃게찜", "꽃게무침"),
    "해물찜": ("아귀찜", "아구찜", ("아귀", "찜"), ("아구", "찜"), "해물파전"),
    "해물파전": ("김치전",),
    "골뱅이무침": ("골뱅이소면",),
    "닭발": ("삼계탕", "육수"),
    **{menu: KIMCHI_DISH for menu in KIMCHI_MENUS},
}

ADDITIONAL_MENUS = {
    "고등어구이", "오징어볶음", "고등어조림", "갈치조림", "두부조림",
    "청국장찌개", "동태찌개", "꽃게탕", "아귀찜", "해물찜", "해물파전", "꼬막무침",
    "도토리묵무침", "황태해장국", "선지해장국", "멸치볶음", "어묵볶음", "진미채볶음",
    "감자채볶음", "소시지야채볶음", "골뱅이무침", "닭발", "닭똥집볶음", "코다리조림", "북엇국",
}
ADDITIONAL_GENERIC_EXCLUDE = ("정식", "백반", "한상")


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()  # "la갈비"도 "LA갈비"로 본다


def keeps(menu: str, title: str) -> bool:
    t = compact(title)
    if menu in ADDITIONAL_MENUS and any(word in t for word in ADDITIONAL_GENERIC_EXCLUDE):
        return False
    for exclusion in TITLE_EXCLUDE.get(menu, ()):
        words = (exclusion,) if isinstance(exclusion, str) else exclusion
        if all(compact(word) in t for word in words):
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
    parser.add_argument("--rejected-out", help="제외된 행을 저장할 선택 CSV 경로")
    args = parser.parse_args()

    with open(args.input_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    kept = [r for r in rows if keeps(r["menu_query"], r["recipe_title"])]
    rejected = [r for r in rows if not keeps(r["menu_query"], r["recipe_title"])]
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
    if args.rejected_out:
        Path(args.rejected_out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.rejected_out, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rejected)
    print(f"행 {len(rows)} → {len(kept)}: {args.out}")


if __name__ == "__main__":
    main()
