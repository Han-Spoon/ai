from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError

from ai_normalization.cleaning import NameCleaningAgent, clean_menu_name
from ai_normalization.contracts import NameCleaningRequest, NameResolution

TRACE_ID = "0199a2f0-0000-7000-8000-000000000001"


def context() -> dict:
    return {
        "schema_version": "3.0",
        "trace_id": TRACE_ID,
        "scan_session_id": "scan-123",
        "call_scope": "scan",
        "item_id": None,
    }


def item(index: int, name: str, *, locale: str | None = "ko") -> dict:
    return {
        "item_id": f"scan-123:{index}",
        "source_index": index,
        "raw_menu_name": name,
        "description": None,
        "price_text": None,
        "price_options": [],
        "ocr_confidence": 0.93,
        "locale_hint": locale,
        "risk_tag_observations": [],
    }


@pytest.mark.parametrize(
    ("raw", "display", "normalized", "options"),
    [
        ("■ 김치 찌개 8,000원", "김치 찌개", "김치찌개", []),
        ("돈까스", "돈까스", "돈가스", []),
        ("부대찌개 2인", "부대찌개", "부대찌개", ["2인"]),
        ("부대찌개 대자", "부대찌개", "부대찌개", ["대자"]),
        ("1. 불고기", "불고기", "불고기", []),
        ("① 불고기", "불고기", "불고기", []),
        ("BEST · 불고기", "불고기", "불고기", []),
        ("된장찌개(1인)", "된장찌개", "된장찌개", ["1인"]),
        ("된장찌개[2인]", "된장찌개", "된장찌개", ["2인"]),
        ("감자탕(大)", "감자탕", "감자탕", ["대"]),
        ("감자탕 대짜", "감자탕", "감자탕", ["대자"]),
        ("냉면 곱배기", "냉면", "냉면", ["곱빼기"]),
        ("삼겹살 500g", "삼겹살", "삼겹살", ["500g"]),
        ("볶음밥 2개이상", "볶음밥", "볶음밥", ["2개 이상"]),
        ("개성식손만두4개", "개성식손만두", "개성식손만두", ["4개"]),
        ("개성식손만두6개", "개성식손만두", "개성식손만두", ["6개"]),
        ("부대찌개2인", "부대찌개", "부대찌개", ["2인"]),
        ("삼겹살1500g", "삼겹살", "삼겹살", ["1500g"]),
        ("통닭1마리", "통닭", "통닭", ["1마리"]),
        ("김치찌개8000원", "김치찌개", "김치찌개", []),
        ("김치찌개(8,O00원)", "김치찌개", "김치찌개", []),
        ("소고기국밥 호주산", "소고기국밥", "소고기국밥", []),
        ("육회국내산 암소한우", "육회", "육회", []),
        ("Bulgogi 불고기", "Bulgogi 불고기", "불고기", []),
        ("차돌된장찌개", "차돌된장찌개", "차돌된장찌개", []),
        ("대구탕", "대구탕", "대구탕", []),
    ],
)
def test_deterministic_cleaning_examples(
    raw: str, display: str, normalized: str, options: list[str]
) -> None:
    result = clean_menu_name(raw)
    assert result.display_name == display
    assert result.normalized_menu_name == normalized
    assert list(result.options) == options


def test_ingredient_option_is_never_removed() -> None:
    result = clean_menu_name("라면 치즈 추가")
    assert result.display_name == "라면 치즈 추가"
    assert result.normalized_menu_name == "라면치즈추가"
    assert result.options == ("치즈 추가",)
    assert result.residual_tokens == ("치즈",)


def test_leading_number_requires_list_punctuation_and_space() -> None:
    assert clean_menu_name("1) 불고기").normalized_menu_name == "불고기"
    assert clean_menu_name("①불고기").status == "cleaned"
    assert clean_menu_name("24시 뼈해장국").display_name == "24시 뼈해장국"
    assert clean_menu_name("1.5인분 피자").display_name == "1.5인분 피자"


def test_price_without_currency_requires_a_separating_space() -> None:
    assert clean_menu_name("김치찌개 8000").normalized_menu_name == "김치찌개"
    assert clean_menu_name("김치찌개8000").normalized_menu_name == "김치찌개8000"


def test_terminal_option_requires_a_real_menu_prefix() -> None:
    assert clean_menu_name("대").normalized_menu_name == "대"
    assert clean_menu_name("500g").normalized_menu_name == "500g"
    assert clean_menu_name("특 갈비탕").normalized_menu_name == "특갈비탕"


def test_origin_like_text_is_removed_only_as_a_known_suffix() -> None:
    assert clean_menu_name("국내산 한우육회").display_name == "국내산 한우육회"
    assert clean_menu_name("자연산광어회").display_name == "자연산광어회"


def test_nonduplicate_mixed_language_name_is_preserved_for_llm() -> None:
    result = clean_menu_name("Bulgogi 덮밥")
    assert result.normalized_menu_name == "bulgogi덮밥"
    assert result.dictionary_hit is False


def test_header_is_invalid_but_unknown_single_character_is_valid() -> None:
    invalid = clean_menu_name("식사류")
    assert invalid.status == "invalid"
    assert invalid.normalized_menu_name is None

    valid = clean_menu_name("죽")
    assert valid.status == "unchanged"
    assert valid.normalized_menu_name == "죽"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("돈까스", "돈가스"),
        ("김치찌게", "김치찌개"),
        ("가재미탕", "가자미탕"),
        ("Bibimbap", "비빔밥"),
        ("BULGOGI", "불고기"),
        ("Kimchi Jjigae", "김치찌개"),
        ("Kimchi-Jjigae", "김치찌개"),
        ("Kimchi Stew", "김치찌개"),
        ("Doenjang Jjigae", "된장찌개"),
        ("Doenjang-Stew", "된장찌개"),
        ("Gimbap", "김밥"),
        ("Kimbap", "김밥"),
        ("Japchae", "잡채"),
        ("Samgyeopsal", "삼겹살"),
        ("Tteokbokki", "떡볶이"),
    ],
)
def test_documented_dictionary_entries(raw: str, expected: str) -> None:
    result = clean_menu_name(raw)
    assert result.normalized_menu_name == expected
    assert result.dictionary_hit is True


@pytest.mark.parametrize(
    "header",
    [
        "메뉴",
        "메뉴판",
        "식사류",
        "안주류",
        "주류",
        "음료",
        "음료류",
        "추천 메뉴",
        "대표 메뉴",
    ],
)
def test_documented_section_headers_are_invalid(header: str) -> None:
    result = clean_menu_name(header)
    assert result.status == "invalid"
    assert result.warning_code == "section_header"


@pytest.mark.parametrize(
    "beverage",
    [
        # Alcohol types, regional products, brands, and package notation
        "소주",
        "참이슬 후레쉬 360ml",
        "새로 오미자",
        "생맥주 500cc",
        "카스(병)",
        "카스 0.0",
        "서울 장수 막걸리 750ml",
        "복분자주",
        "청하",
        "연태고량주",
        "하이볼",
        "와인",
        # Carbonated, traditional, coffee/tea, water, and energy drinks
        "제로 콜라",
        "칠성사이다 355ml",
        "환타 오렌지",
        "환타 포도",
        "웰치스 포도",
        "데미소다 사과",
        "오렌지 주스",
        "자몽 에이드",
        "식혜",
        "수정과",
        "미숫가루",
        "매실",
        "유자",
        "대추",
        "생강",
        "모과",
        "오미자",
        "복분자",
        "아메리카노",
        "녹차",
        "삼다수",
        "포카리스웨트",
        # Common foreign-script labels
        "Coca-Cola",
        "Cass 500ml",
        "Cass 0.0",
        "Beer",
        "Soju",
        "Makgeolli",
        "ビール",
        "啤酒",
    ],
)
def test_beverage_items_are_invalid(beverage: str) -> None:
    result = clean_menu_name(beverage)

    assert result.status == "invalid"
    assert result.normalized_menu_name is None
    assert result.warning_code == "beverage_item"


@pytest.mark.parametrize(
    "food_or_mixed_item",
    [
        "막걸리파전",
        "소주삼겹살세트",
        "치킨+맥주",
        "콜라찜닭",
        "녹차아이스크림",
        "커피콩빵",
        "와인소스스테이크",
        "청주식 돼지짜글이",
        "새로구이",
        "밀키스맛빵",
        "환타맛치킨",
        "매실장아찌",
        "유자드레싱샐러드",
        "대추갈비찜",
        "생강돼지구이",
        "모과소스탕수육",
        "오미자소스샐러드",
        "복분자소스불고기",
    ],
)
def test_beverage_words_do_not_remove_food_items(food_or_mixed_item: str) -> None:
    result = clean_menu_name(food_or_mixed_item)

    assert result.status != "invalid"
    assert result.normalized_menu_name is not None


@pytest.mark.parametrize("beverage_combo", ["소주+맥주", "콜라/사이다"])
def test_all_beverage_combinations_are_invalid(beverage_combo: str) -> None:
    result = clean_menu_name(beverage_combo)

    assert result.status == "invalid"
    assert result.warning_code == "beverage_item"


@pytest.mark.parametrize(
    "text",
    [
        "1인분 추가 3,000원",
        "오늘의 메뉴",
        "셀프 코너 이용",
    ],
)
def test_non_menu_lines_are_invalid(text: str) -> None:
    result = clean_menu_name(text)

    assert result.status == "invalid"
    assert result.normalized_menu_name is None


def test_batch_preserves_fields_and_returns_source_order() -> None:
    valid_item = item(0, "■ Kimchi 김치 찌개 2인")
    valid_item["origin_text"] = "국내산"
    valid_item["foreign_text_segments"] = [
        {"text": "Kimchi", "role": "unknown", "confidence": 0.0}
    ]
    payload = {
        "context": context(),
        "items": [item(1, "식사류"), valid_item],
        "warnings": [],
        "errors": [],
    }
    response = asyncio.run(NameCleaningAgent().run(NameCleaningRequest.model_validate(payload)))

    assert [value.source_index for value in response.items] == [0, 1]
    assert response.items[0].raw_menu_name == "■ Kimchi 김치 찌개 2인"
    assert response.items[0].origin_text == "국내산"
    assert response.items[0].foreign_text_segments[0].role == "unknown"
    assert response.items[0].ocr_confidence == 0.93
    assert response.items[1].cleaning_status == "invalid"
    assert response.warnings[0].code == "section_header"


def test_cleaning_request_rejects_store_context_and_unknown_fields() -> None:
    payload = {"context": context(), "items": [item(0, "김치찌개")], "warnings": [], "errors": []}
    with_store = deepcopy(payload)
    with_store["context"]["store_id"] = 1
    with pytest.raises(ValidationError):
        NameCleaningRequest.model_validate(with_store)

    with_null_store = deepcopy(payload)
    with_null_store["context"]["store_id"] = None
    with pytest.raises(ValidationError):
        NameCleaningRequest.model_validate(with_null_store)

    with_unknown = deepcopy(payload)
    with_unknown["items"][0]["unknown"] = True
    with pytest.raises(ValidationError):
        NameCleaningRequest.model_validate(with_unknown)


class UnsafeNameResolver:
    async def resolve_name(self, item):
        return NameResolution(
            normalized_menu_name="라면",
            confidence=0.99,
            foreign_text_segments=[
                segment.model_copy(
                    update={"role": "translation", "confidence": 0.99}
                )
                for segment in item.foreign_text_segments
            ],
        )


class EnglishNameResolver:
    def __init__(self) -> None:
        self.received = None

    async def resolve_name(self, item):
        self.received = item
        return NameResolution(
            normalized_menu_name="치킨",
            confidence=0.99,
            foreign_text_segments=[
                segment.model_copy(
                    update={"role": "translation", "confidence": 0.99}
                )
                for segment in item.foreign_text_segments
            ],
        )


def test_known_bilingual_duplicate_skips_llm() -> None:
    resolver = EnglishNameResolver()
    payload = {
        "context": context(),
        "items": [item(0, "Bulgogi 불고기", locale="ko")],
        "warnings": [],
        "errors": [],
    }
    response = asyncio.run(
        NameCleaningAgent(resolver).run(NameCleaningRequest.model_validate(payload))
    )
    assert response.items[0].normalized_menu_name == "불고기"
    assert response.items[0].foreign_text_segments[0].role == "translation"
    assert resolver.received is None


class ConcurrentNameResolver:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def resolve_name(self, item):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if item.display_name == "Fail":
                raise RuntimeError("resolver failure")
            return NameResolution(
                normalized_menu_name="번역메뉴",
                confidence=0.99,
                foreign_text_segments=[
                    segment.model_copy(
                        update={"role": "translation", "confidence": 0.99}
                    )
                    for segment in item.foreign_text_segments
                ],
            )
        finally:
            self.active -= 1


def test_llm_cannot_delete_korean_ingredient_tokens() -> None:
    payload = {
        "context": context(),
        "items": [item(0, "Cheese 치즈 라면", locale="en")],
        "warnings": [],
        "errors": [],
    }
    response = asyncio.run(
        NameCleaningAgent(UnsafeNameResolver()).run(NameCleaningRequest.model_validate(payload))
    )
    assert response.items[0].normalized_menu_name == "cheese치즈라면"
    assert response.warnings[0].code == "name_llm_failed"


def test_unknown_latin_name_uses_minimal_resolver_input() -> None:
    resolver = EnglishNameResolver()
    payload = {
        "context": context(),
        "items": [item(0, "Chicken", locale="ko")],
        "warnings": [],
        "errors": [],
    }
    response = asyncio.run(
        NameCleaningAgent(resolver).run(NameCleaningRequest.model_validate(payload))
    )
    assert response.items[0].normalized_menu_name == "치킨"
    assert set(resolver.received.model_dump()) == {
        "display_name",
        "normalized_menu_name",
        "locale_hint",
        "residual_tokens",
        "foreign_text_segments",
    }


def test_cjk_name_is_valid_and_uses_multilingual_resolver() -> None:
    resolver = EnglishNameResolver()
    payload = {
        "context": context(),
        "items": [item(0, "チキン", locale=None)],
        "warnings": [],
        "errors": [],
    }
    response = asyncio.run(
        NameCleaningAgent(resolver).run(NameCleaningRequest.model_validate(payload))
    )

    assert resolver.received.display_name == "チキン"
    assert response.items[0].normalized_menu_name == "치킨"


class SemanticDetailResolver:
    async def resolve_name(self, item):
        return NameResolution(
            normalized_menu_name="소고기불고기",
            confidence=0.99,
            foreign_text_segments=[
                segment.model_copy(
                    update={"role": "semantic_detail", "confidence": 0.99}
                )
                for segment in item.foreign_text_segments
            ],
        )


def test_semantic_detail_is_preserved_as_a_residual_token() -> None:
    payload = {
        "context": context(),
        "items": [item(0, "Beef 불고기", locale="en")],
        "warnings": [],
        "errors": [],
    }
    response = asyncio.run(
        NameCleaningAgent(SemanticDetailResolver()).run(
            NameCleaningRequest.model_validate(payload)
        )
    )

    cleaned = response.items[0]
    assert cleaned.normalized_menu_name == "소고기불고기"
    assert cleaned.foreign_text_segments[0].role == "semantic_detail"
    assert cleaned.residual_tokens == ["Beef"]


class UnknownChangingResolver:
    async def resolve_name(self, item):
        return NameResolution(
            normalized_menu_name="임의메뉴",
            confidence=0.99,
            foreign_text_segments=list(item.foreign_text_segments),
        )


def test_unknown_foreign_segment_cannot_change_the_search_name() -> None:
    payload = {
        "context": context(),
        "items": [item(0, "Mystery", locale="en")],
        "warnings": [],
        "errors": [],
    }
    response = asyncio.run(
        NameCleaningAgent(UnknownChangingResolver()).run(
            NameCleaningRequest.model_validate(payload)
        )
    )

    assert response.items[0].normalized_menu_name == "mystery"
    assert response.items[0].foreign_text_segments[0].role == "unknown"
    assert response.warnings[0].code == "name_llm_failed"


def test_name_llm_calls_are_bounded_ordered_and_failure_isolated() -> None:
    resolver = ConcurrentNameResolver()
    payload = {
        "context": context(),
        "items": [
            item(2, "Burger", locale="en"),
            item(0, "Chicken", locale="en"),
            item(1, "Fail", locale="en"),
        ],
        "warnings": [],
        "errors": [],
    }

    response = asyncio.run(
        NameCleaningAgent(resolver, max_concurrent_llm_calls=2).run(
            NameCleaningRequest.model_validate(payload)
        )
    )

    assert resolver.max_active == 2
    assert [value.source_index for value in response.items] == [0, 1, 2]
    assert response.items[0].normalized_menu_name == "번역메뉴"
    assert response.items[1].normalized_menu_name == "fail"
    assert response.items[2].normalized_menu_name == "번역메뉴"
    assert [warning.item_id for warning in response.warnings] == ["scan-123:1"]
    assert response.warnings[0].code == "name_llm_failed"


@pytest.mark.parametrize("value", [0, 11, True, 1.5])
def test_name_llm_concurrency_must_be_between_one_and_ten(value) -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        NameCleaningAgent(max_concurrent_llm_calls=value)


def test_configured_low_ocr_confidence_is_a_safety_warning() -> None:
    source = item(0, "김치찌개")
    source["ocr_confidence"] = 0.4
    payload = {"context": context(), "items": [source], "warnings": [], "errors": []}
    response = asyncio.run(
        NameCleaningAgent(low_ocr_confidence_threshold=0.7).run(
            NameCleaningRequest.model_validate(payload)
        )
    )
    assert response.warnings[0].code == "low_ocr_confidence"
    assert response.warnings[0].forces_caution is True


def test_default_low_ocr_threshold_is_documented_value() -> None:
    source = item(0, "김치찌개")
    source["ocr_confidence"] = 0.64
    payload = {"context": context(), "items": [source], "warnings": [], "errors": []}
    response = asyncio.run(NameCleaningAgent().run(NameCleaningRequest.model_validate(payload)))
    assert response.warnings[0].code == "low_ocr_confidence"


@pytest.mark.parametrize("raw", ["02-123-4567", "영업시간 09:00~21:00"])
def test_phone_and_business_hours_are_invalid(raw: str) -> None:
    payload = {
        "context": context(),
        "items": [item(0, raw)],
        "warnings": [],
        "errors": [],
    }
    response = asyncio.run(NameCleaningAgent().run(NameCleaningRequest.model_validate(payload)))
    assert response.items[0].cleaning_status == "invalid"
    assert response.warnings[0].code == "invalid_menu_text"


def test_low_ocr_warning_is_not_created_for_excluded_invalid_item() -> None:
    source = item(0, "식사류")
    source["ocr_confidence"] = 0.1
    payload = {"context": context(), "items": [source], "warnings": [], "errors": []}
    response = asyncio.run(NameCleaningAgent().run(NameCleaningRequest.model_validate(payload)))
    assert [warning.code for warning in response.warnings] == ["section_header"]


def test_beverage_warning_replaces_low_ocr_warning_for_excluded_item() -> None:
    source = item(0, "참이슬 360ml")
    source["ocr_confidence"] = 0.1
    payload = {"context": context(), "items": [source], "warnings": [], "errors": []}

    response = asyncio.run(NameCleaningAgent().run(NameCleaningRequest.model_validate(payload)))

    assert response.items[0].cleaning_status == "invalid"
    assert response.items[0].display_name == "참이슬 360ml"
    assert [warning.code for warning in response.warnings] == ["beverage_item"]
    assert response.warnings[0].forces_caution is False
