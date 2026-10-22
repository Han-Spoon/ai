from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from ai_normalization.candidate_policy import CandidateSelectionAgent
from ai_normalization.contracts import CandidateResolution, CandidateSelectionRequest

TRACE_ID = "0199a2f0-0000-7000-8000-000000000001"


def candidate(menu_id: int, score: float, match_type: str = "fuzzy") -> dict:
    return {
        "menu_id": menu_id,
        "menu_category_id": 11,
        "canonical_name": f"메뉴{menu_id}",
        "score": score,
        "match_type": match_type,
    }


def selection_item(
    candidates: list[dict], *, status: str = "ok", normalized_name: str | None = "김치찌개"
) -> dict:
    return {
        "item_id": "scan-123:0",
        "source_index": 0,
        "raw_menu_name": "김치 찌개",
        "normalized_menu_name": normalized_name,
        "display_name": "김치 찌개",
        "description": "돼지고기가 들어간 찌개",
        "price_text": "8,000원",
        "price_options": [],
        "ocr_confidence": 0.93,
        "locale_hint": "ko",
        "options": [],
        "residual_tokens": [],
        "risk_tag_observations": [],
        "candidate_status": status,
        "match_candidates": candidates,
    }


def request(item: dict, *, store_id=None) -> CandidateSelectionRequest:
    context = {
        "schema_version": "3.0",
        "trace_id": TRACE_ID,
        "scan_session_id": "scan-123",
        "store_id": store_id,
        "call_scope": "scan",
        "item_id": None,
    }
    return CandidateSelectionRequest.model_validate(
        {"context": context, "items": [item], "warnings": [], "errors": []}
    )


def run(item: dict, resolver=None):
    return asyncio.run(CandidateSelectionAgent(resolver).run(request(item)))


def test_unique_canonical_candidate_is_selected_verbatim() -> None:
    source = candidate(1001, 1.0, "canonical")
    result = run(selection_item([source])).items[0]
    assert result.normalization_status == "normalized"
    assert result.selection_method == "deterministic"
    assert result.selection_reason_codes == ["exact_unique"]
    assert result.selected_candidate.model_dump() == source


def test_fuzzy_candidate_with_score_and_margin_is_selected() -> None:
    result = run(selection_item([candidate(1, 0.93), candidate(2, 0.80)])).items[0]
    assert result.normalization_status == "normalized"
    assert result.selection_reason_codes == ["score_and_margin"]


def test_close_candidates_remain_ambiguous_without_resolver() -> None:
    response = run(selection_item([candidate(1, 0.91), candidate(2, 0.87)]))
    assert response.items[0].normalization_status == "ambiguous"
    assert response.items[0].selected_candidate is None
    assert response.items[0].selection_reason_codes == ["multiple_close_candidates"]
    assert response.warnings[0].forces_caution is True


def test_below_floor_and_not_found_are_unmatched() -> None:
    below = run(selection_item([candidate(1, 0.60)])).items[0]
    assert below.normalization_status == "unmatched"
    assert below.selection_reason_codes == ["below_accept_threshold"]

    missing = run(selection_item([], status="not_found")).items[0]
    assert missing.normalization_status == "unmatched"
    assert missing.selection_reason_codes == ["candidate_not_found"]


def test_unavailable_adds_safety_warning_and_storeless_is_preserved() -> None:
    response = asyncio.run(
        CandidateSelectionAgent().run(request(selection_item([], status="unavailable"), store_id=None))
    )
    assert response.context.store_id is None
    assert response.items[0].selection_reason_codes == ["candidate_unavailable"]
    assert response.warnings[0].code == "candidate_unavailable"
    assert response.warnings[0].forces_caution is True


class ValidResolver:
    def __init__(self) -> None:
        self.received = None

    async def resolve_candidate(self, _item):
        self.received = _item
        return CandidateResolution(
            selected_menu_id=2,
            confidence=0.93,
            reason_code="description_supports_candidate",
        )


class OutsideResolver:
    async def resolve_candidate(self, _item):
        return CandidateResolution(
            selected_menu_id=999,
            confidence=0.99,
            reason_code="description_supports_candidate",
        )


def test_llm_may_select_only_an_existing_candidate() -> None:
    source = selection_item([candidate(1, 0.91), candidate(2, 0.87)])
    resolver = ValidResolver()
    selected = run(source, resolver).items[0]
    assert selected.normalization_status == "normalized"
    assert selected.selection_method == "llm"
    assert selected.selected_candidate.menu_id == 2
    assert selected.selection_reason_codes == ["llm_context_resolved"]
    assert selected.model_dump()["selected_candidate"] == source["match_candidates"][1]
    resolver_payload = resolver.received.model_dump()
    assert set(resolver_payload) == {
        "raw_menu_name",
        "normalized_menu_name",
        "display_name",
        "description",
        "locale_hint",
        "options",
        "residual_tokens",
        "candidates",
    }
    assert set(resolver_payload["candidates"][0]) == {"menu_id", "canonical_name"}

    rejected = run(source, OutsideResolver())
    assert rejected.items[0].normalization_status == "ambiguous"
    assert rejected.items[0].selected_candidate is None
    assert any(warning.code == "invalid_llm_candidate" for warning in rejected.warnings)


def test_contract_rejects_more_than_five_or_unsorted_candidates() -> None:
    with pytest.raises(ValidationError):
        request(selection_item([candidate(index, 0.9) for index in range(1, 7)]))

    with pytest.raises(ValidationError):
        request(selection_item([candidate(1, 0.8), candidate(2, 0.9)]))


def test_candidate_selection_requires_explicit_store_id_or_null() -> None:
    payload = request(selection_item([candidate(1, 1.0, "canonical")])).model_dump(
        mode="json"
    )
    del payload["context"]["store_id"]
    with pytest.raises(ValidationError):
        CandidateSelectionRequest.model_validate(payload)


class UnsupportedContextResolver:
    async def resolve_candidate(self, _item):
        return CandidateResolution(
            selected_menu_id=1,
            confidence=0.99,
            reason_code="option_supports_candidate",
        )


def test_llm_reason_must_have_its_documented_context() -> None:
    response = run(
        selection_item([candidate(1, 0.91), candidate(2, 0.87)]),
        UnsupportedContextResolver(),
    )
    assert response.items[0].normalization_status == "ambiguous"
    assert any(warning.code == "invalid_llm_candidate" for warning in response.warnings)
