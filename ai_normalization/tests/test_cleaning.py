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
        ("Kimchi Stew", "김치찌개"),
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


def test_batch_preserves_fields_and_returns_source_order() -> None:
    payload = {
        "context": context(),
        "items": [item(1, "식사류"), item(0, "■ 김치 찌개 2인")],
        "warnings": [],
        "errors": [],
    }
    response = asyncio.run(NameCleaningAgent().run(NameCleaningRequest.model_validate(payload)))

    assert [value.source_index for value in response.items] == [0, 1]
    assert response.items[0].raw_menu_name == "■ 김치 찌개 2인"
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
    async def resolve_name(self, _item):
        return NameResolution(normalized_menu_name="라면", confidence=0.99)


class EnglishNameResolver:
    def __init__(self) -> None:
        self.received = None

    async def resolve_name(self, item):
        self.received = item
        return NameResolution(normalized_menu_name="치킨", confidence=0.99)


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
    }


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
