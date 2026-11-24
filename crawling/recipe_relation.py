# -*- coding: utf-8 -*-
"""레시피 제목이 검색 메뉴 자체인지 보수적으로 분류한다.

검색 사이트는 ``불고기`` 결과에 불고기김밥·불고기피자를, ``순대`` 결과에
오징어순대·순대쌈장을 함께 돌려준다. 제목에 검색어가 있다는 이유만으로 모두
기본 메뉴 prior에 합치면 다른 음식의 재료가 섞인다. 이 모듈은 그 판단을 한 곳에
모으고, 확실한 동일 메뉴(``exact``)만 기본 prior에 들어가게 한다.

불확실하거나 변형인 레시피는 버리지 않는다. 호출자가 감사 CSV에 남겨 사람이
원제목과 URL을 확인할 수 있게 관계와 이유를 반환한다.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

try:  # package import / direct script import both work
    from .filter_titles_additional_25 import TITLE_EXCLUDE, TITLE_KEYWORDS
except ImportError:  # pragma: no cover - direct ``python crawling/...`` execution
    from filter_titles_additional_25 import TITLE_EXCLUDE, TITLE_KEYWORDS


ROOT = Path(__file__).resolve().parent

# 기존 76개 필터와 추가 메뉴 필터가 한동안 별도 파일로 관리되어 일부 별칭이 한쪽에만
# 있었다. 최종 관계 분류기는 두 목록의 합집합을 사용한다. 새 별칭은 이 공통 목록에
# 추가해야 크롤링 사이트와 관계없이 같은 결과가 난다.
SAME_MENU_ALIASES = dict(TITLE_KEYWORDS)
SAME_MENU_ALIASES.update(
    {
        "불고기": ("불고기", "소불고기", "쇠불고기"),
        "라볶이": ("라볶이", "라뽁이", "라뽂이", "라볶기"),
        "떡꼬치": ("떡꼬치", "떡꼬지", ("떡볶이", "꼬치")),
        "간장게장": (("간장", "게장"),),
        "양념게장": (("양념", "게장"),),
        "깻잎장아찌": (
            ("깻잎", "장아찌"),
            ("깻잎", "짱아찌"),
            ("깻잎", "짱아치"),
            "꺳잎장아찌",
        ),
        "곱창전골": (("곱창", "전골"),),
        "전복죽": (("전복", "죽"),),
        "김치찌개": ("김치찌개", "김치찌게"),
        "된장찌개": ("된장찌개", "된장찌게"),
        "순두부찌개": ("순두부찌개", "순두부찌게"),
        "부대찌개": ("부대찌개", "부대찌게"),
        "갈비찜": ("갈비찜", "찜갈비"),
        "슈크림 붕어빵": (
            ("슈크림", "붕어빵"),
            ("커스터드", "붕어빵"),
            ("크림", "붕어빵"),
        ),
        "감자전": ("감자전", "감자부침개"),
        "애호박전": ("애호박전", "애호박부침개"),
        "생선전": ("생선전", "동태전", "생선지짐"),
        "동그랑땡": ("동그랑땡", "동그랭땡", "돈저냐"),
    }
)

RELATION_TITLE_EXCLUDE = dict(TITLE_EXCLUDE)
RELATION_TITLE_EXCLUDE.update(
    {
        "간장게장": ("양념게장",),
        "양념게장": ("간장게장",),
    }
)

# 검색 메뉴 뒤에 붙으면 별개의 요리로 보는 형태. 한 글자(국·전·탕)는 일반 문장과
# 충돌하기 쉬워 알려진 메뉴명 매칭 또는 붙어 있는 접미사 검사에서만 사용한다.
DISH_FORMS = {
    "강정",
    "겉절이",
    "그라탕",
    "김치찜",
    "김밥",
    "덮밥",
    "두루치기",
    "라면",
    "라따뚜이",
    "리조또",
    "말이",
    "무침",
    "베이크",
    "버거",
    "볶음",
    "볶음밥",
    "부침개",
    "샐러드",
    "샌드위치",
    "소면",
    "스파게티",
    "스튜",
    "쌈장",
    "쌈밥",
    "쌀밥",
    "우동",
    "유부초밥",
    "절임",
    "전골",
    "정식",
    "조림",
    "주먹밥",
    "짜글이",
    "찌개",
    "칼국수",
    "카레",
    "커리",
    "퀘사디아",
    "타코",
    "토스트",
    "토르티야",
    "파니니",
    "파스타",
    "프라이",
    "피자",
    "초밥",
    "튀김",
}

DISH_FORM_GUARDS = {
    "프라이": ("에어프라이어", "에어프라이", "프라이팬", "후라이팬"),
}

ATTACHED_DISH_SUFFIXES = DISH_FORMS | {"국", "면", "밥", "빵", "전", "죽", "찜", "탕"}

# 메뉴명에 바로 붙어도 음식 정체성을 바꾸지 않는 제목용 수식어다. 이 목록에 없는
# 붙임말은 무리하게 exact로 넣지 않고 ambiguous로 격리한다.
BENIGN_PREFIXES = (
    "백종원표",
    "백종원",
    "수미네반찬",
    "알토란",
    "엄마표",
    "홈메이드",
    "초간단",
    "간단",
    "정통",
    "옛날식",
    "서울식",
    "매콤한",
    "맛있는",
)
BENIGN_SUFFIXES = (
    "황금레시피",
    "레시피",
    "맛있게만들기",
    "만드는방법",
    "만드는법",
    "만들기",
    "요리법",
)

VARIANT_WORDS = {
    "가지",
    "고기",
    "김치",
    "돼지",
    "매운",
    "소고기",
    "쇠고기",
    "야채",
    "오징어",
    "찰",
    "참치",
    "채소",
    "치즈",
    "피",
    "톳",
    "해초",
    "해물",
}

FINISHED_DISH_PREFIXES = (
    "먹다남은",
    "먹고남은",
    "완성된",
    "만들어둔",
    "만들어놓은",
    "준비한",
    "시판",
    "남은",
)


@dataclass(frozen=True)
class RecipeRelation:
    relation: str  # exact | variant | other_dish | unrelated | ambiguous | derivative
    resolved_menu_name: str | None
    base_menu_name: str | None
    remain_token: str | None
    reason: str

    @property
    def included_in_base_prior(self) -> bool:
        return self.relation == "exact"


def compact(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^0-9a-z가-힣]+", "", normalized)


def words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.findall(r"[0-9a-z가-힣]+", normalized)


def _patterns(menu: str) -> tuple[str | tuple[str, ...], ...]:
    return (menu, *SAME_MENU_ALIASES.get(menu, ()))


def _pattern_span(text: str, pattern: str | tuple[str, ...]) -> tuple[int, int] | None:
    """공백·기호를 없앤 제목에서 패턴이 차지하는 가장 좁은 구간을 찾는다."""

    value = compact(text)
    parts = (pattern,) if isinstance(pattern, str) else pattern
    needles = [compact(part) for part in parts if compact(part)]
    if not needles:
        return None
    cursor = 0
    start: int | None = None
    for needle in needles:
        index = value.find(needle, cursor)
        if index < 0:
            return None
        if start is None:
            start = index
        cursor = index + len(needle)
    return start or 0, cursor


def _best_span(title: str, menu: str) -> tuple[tuple[int, int], str] | None:
    matches: list[tuple[tuple[int, int], str]] = []
    for pattern in _patterns(menu):
        span = _pattern_span(title, pattern)
        if span is not None:
            label = " ".join(pattern) if isinstance(pattern, tuple) else pattern
            matches.append((span, label))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0][1] - item[0][0])


def _contains_pattern(title: str, pattern: str | tuple[str, ...]) -> bool:
    return _pattern_span(title, pattern) is not None


@lru_cache(maxsize=1)
def ingredient_terms() -> frozenset[str]:
    terms = set(VARIANT_WORDS)
    path = ROOT / "ingredient_taxonomy.csv"
    with path.open(encoding="utf-8-sig") as handle:
        terms.update(row["name_ko"] for row in csv.DictReader(handle) if row.get("name_ko"))
    return frozenset(compact(term) for term in terms if len(compact(term)) >= 1)


@lru_cache(maxsize=1)
def known_menu_names() -> frozenset[str]:
    """사이트별 입력 범위와 무관한 전체 메뉴 사전.

    한 사이트에 특정 메뉴 검색 결과가 없더라도 그 메뉴명이 다른 검색 결과 제목에
    나오면 다른 음식으로 잡혀야 한다. 호출 시 받은 메뉴만 쓰면 사이트별 prior와
    merged prior의 분류가 달라지므로 버전 관리하는 전체 검색 메뉴를 함께 쓴다.
    """

    names = set(SAME_MENU_ALIASES)
    for filename in ("menu_queries.txt", "menu_queries_additional_25.txt"):
        path = ROOT / filename
        if path.exists():
            names.update(line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip())
    return frozenset(names)


def _strip_benign(text: str) -> str:
    before = None
    while text and text != before:
        before = text
        for prefix in BENIGN_PREFIXES:
            value = compact(prefix)
            if text.startswith(value):
                text = text[len(value) :]
        for suffix in BENIGN_SUFFIXES:
            value = compact(suffix)
            if text.endswith(value):
                text = text[: -len(value)]
    return text


def _attached_residue(title: str, aliases: Iterable[str]) -> tuple[str, str] | None:
    """메뉴 별칭과 같은 띄어쓰기 토큰에 붙은 앞·뒤 문자열을 반환한다."""

    candidates: list[tuple[int, str, str]] = []
    for token in words(title):
        token_key = compact(token)
        for alias in aliases:
            alias_key = compact(alias)
            if not alias_key or alias_key not in token_key:
                continue
            index = token_key.find(alias_key)
            candidates.append((len(alias_key), token_key[:index], token_key[index + len(alias_key) :]))
    if not candidates:
        return None
    _, prefix, suffix = max(candidates)
    return _strip_benign(prefix), _strip_benign(suffix)


def classify_recipe_title(
    menu: str,
    title: str,
    known_menus: Iterable[str] = (),
) -> RecipeRelation:
    """제목과 검색 메뉴의 관계를 분류한다.

    동일 메뉴의 다른 이름은 ``TITLE_KEYWORDS``로 허용한다. 다른 알려진 메뉴가 더
    길게 잡히거나, 별개 요리 형태가 남거나, 재료 수식어가 붙으면 기본 prior에서
    제외한다. 애매하면 포함시키지 않는 것이 알레르기 FN 정책에도 안전하다. 제외된
    레시피는 0회 관측으로 세지 않아 확률을 낮추지 않기 때문이다.
    """

    target = _best_span(title, menu)
    if target is None:
        return RecipeRelation("unrelated", None, None, None, "title_has_no_menu_alias")
    target_span, matched_alias = target
    title_key = compact(title)

    for exclusion in RELATION_TITLE_EXCLUDE.get(menu, ()):
        if _contains_pattern(title, exclusion):
            label = " ".join(exclusion) if isinstance(exclusion, tuple) else exclusion
            return RecipeRelation("other_dish", label, None, None, f"excluded_title_term:{label}")

    # ``순대`` 검색 결과의 ``오징어순대``처럼 더 구체적인 알려진 메뉴가 있으면
    # 검색어가 들어 있어도 그 메뉴의 레시피다. 반대로 순대국밥 안의 짧은 순대 매칭은
    # 목표 span 내부이므로 무시한다.
    other_matches: list[tuple[bool, int, int, str]] = []
    for other in (set(known_menus) | set(known_menu_names())) - {menu}:
        match = _best_span(title, other)
        if match is None:
            continue
        (start, end), _ = match
        if target_span[0] <= start and end <= target_span[1]:
            continue
        other_matches.append((end == len(title_key), end - start, start, other))
    if other_matches:
        resolved = max(other_matches)[3]
        return RecipeRelation("other_dish", resolved, None, None, f"different_known_menu:{resolved}")

    remainder = title_key[: target_span[0]] + title_key[target_span[1] :]
    for form in sorted(DISH_FORMS, key=lambda value: (len(value), value), reverse=True):
        guarded = remainder
        for phrase in DISH_FORM_GUARDS.get(form, ()):
            guarded = guarded.replace(compact(phrase), "")
        if compact(form) in guarded:
            return RecipeRelation("other_dish", form, None, None, f"different_dish_form:{form}")

    string_aliases = [p for p in _patterns(menu) if isinstance(p, str)]
    attached = _attached_residue(title, string_aliases)
    if attached is not None:
        prefix, suffix = attached
        for form in sorted(ATTACHED_DISH_SUFFIXES, key=lambda value: (len(value), value), reverse=True):
            if form == "밥" and suffix.startswith("밥상"):
                continue
            if suffix.startswith(compact(form)):
                return RecipeRelation("other_dish", form, None, None, f"attached_dish_suffix:{form}")

        modifiers = ingredient_terms()
        prefix_hits = sorted(
            (term for term in modifiers if term and prefix.endswith(term)),
            key=lambda value: (len(value), value),
            reverse=True,
        )
        suffix_hits = sorted(
            (term for term in modifiers if term and suffix.startswith(term)),
            key=lambda value: (len(value), value),
            reverse=True,
        )
        if prefix_hits or suffix_hits:
            remain = (prefix_hits or suffix_hits)[0]
            return RecipeRelation("variant", f"{remain} {menu}", menu, remain, f"ingredient_modifier:{remain}")
        if prefix or suffix:
            residue = " ".join(part for part in (prefix, suffix) if part)
            return RecipeRelation("ambiguous", None, None, residue, f"unknown_attached_text:{residue}")

    # 띄어 쓴 ``가지 순대``도 변형이다. 제목용 수식어는 재료 사전에 없으므로 통과한다.
    title_words = [compact(word) for word in words(title)]
    alias_keys = {compact(alias) for alias in string_aliases}
    for index, token in enumerate(title_words):
        if token not in alias_keys:
            continue
        neighbours = title_words[max(0, index - 1) : index] + title_words[index + 1 : index + 2]
        for neighbour in neighbours:
            if neighbour in ingredient_terms():
                return RecipeRelation(
                    "variant", f"{neighbour} {menu}", menu, neighbour, f"spaced_ingredient_modifier:{neighbour}"
                )

    return RecipeRelation("exact", menu, None, None, f"same_menu_alias:{matched_alias}")


def finished_dish_ingredient(menu: str, raw: str, canonical: str) -> bool:
    """재료 칸의 ``완성된 잡채``·``남은 순대`` 같은 완제품 자기참조를 찾는다."""

    # 제목 별칭을 여기 쓰면 ``막창구이``의 원재료 ``막창``까지 완제품으로 오인한다.
    # 재료 자기참조는 DB 메뉴의 정규 이름과 같은 경우에만 잡는다.
    menu_keys = {compact(menu)}
    for value in (raw, canonical):
        key = compact(value)
        if key in menu_keys:
            return True
        for prefix in FINISHED_DISH_PREFIXES:
            prefix_key = compact(prefix)
            if key.startswith(prefix_key) and key[len(prefix_key) :] in menu_keys:
                return True
    return False
