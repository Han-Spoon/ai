from copy import deepcopy
from typing import Optional

import pytest
from pydantic import ValidationError

from ai_supervisor.contracts import JudgeRequest, JudgeResponse, PrepareRequest, UserProfile
from ai_supervisor.contracts.common import NodeWarning, OwnerQuestionState

TRACE_ID = "0199a2f0-0000-7000-8000-000000000001"
PREPARE_ID = "0199a2f0-0000-7000-8000-000000000101"
HASH = "sha256:" + "a" * 64


def prepared_item() -> dict:
    return {
        "item_id": "scan-123:0",
        "source_index": 0,
        "raw_menu_name": "■ 김치 찌개 2인",
        "normalized_menu_name": "김치찌개",
        "display_name": "김치 찌개",
        "description": None,
        "price_text": "8,000원",
        "price_options": [],
        "ocr_confidence": 0.93,
        "locale_hint": "ko",
        "options": ["2인"],
        "residual_tokens": [],
        "risk_tag_observations": [],
    }


def empty_ontology() -> dict:
    return {
        "status": "not_found",
        "menu": None,
        "recipe_ingredients": [],
        "menu_recipe_corpora": [],
        "menu_ingredient_priors": [],
        "cache": None,
    }


def base_judge_request() -> dict:
    return {
        "schema_version": "3.0",
        "trace_id": TRACE_ID,
        "prepare_id": PREPARE_ID,
        "scan_session_id": "scan-123",
        "store_id": None,
        "prepared_items_hash": HASH,
        "knowledge_revision": "revision-1",
        "prepare_signals": {"scan_quality_status": "usable", "warnings": []},
        "prepared_items": [prepared_item()],
        "knowledge_bundle": {
            "bundle_schema_version": "3.0",
            "ingredient_graph": {
                "status": "not_found",
                "ingredients": [],
                "aliases": [],
                "tags": [],
                "compositions": [],
                "composition_tags": [],
            },
            "items": [
                {
                    "item_id": "scan-123:0",
                    "candidate_lookup": {"status": "not_found", "candidates": []},
                    "candidate_knowledge": [],
                    "fallback_ontology": empty_ontology(),
                    "web_search_cache": {
                        "status": "not_found",
                        "cached_sources": [],
                        "next_rank": 1,
                    },
                }
            ],
            "source_reliability": {"status": "not_found", "weights": []},
        },
        "user_profile": {
            "religion_type": None,
            "is_vegetarian": False,
            "vegetarian_type": None,
            "no_alcohol": False,
            "allergies": [],
            "no_spicy": False,
        },
        "locale": "ko",
    }


@pytest.mark.parametrize(
    ("prepare_signals", "prepared_items", "knowledge_items"),
    [
        (
            {"scan_quality_status": "needs_retake", "warnings": []},
            [prepared_item()],
            None,
        ),
        ({"scan_quality_status": "usable", "warnings": []}, [], []),
    ],
)
def test_judge_rejects_non_analyzable_prepare_results(
    prepare_signals: dict,
    prepared_items: list[dict],
    knowledge_items: Optional[list[dict]],
) -> None:
    payload = base_judge_request()
    payload["prepare_signals"] = prepare_signals
    payload["prepared_items"] = prepared_items
    if knowledge_items is not None:
        payload["knowledge_bundle"]["items"] = knowledge_items

    with pytest.raises(ValidationError):
        JudgeRequest.model_validate(payload)


def store_warning() -> dict:
    return {
        "code": "store_context_missing",
        "node": "supervisor",
        "item_id": "scan-123:0",
        "message": "가게 문맥이 없습니다.",
        "forces_caution": True,
    }


def base_judge_response() -> dict:
    return {
        "schema_version": "3.0",
        "trace_id": TRACE_ID,
        "prepare_id": PREPARE_ID,
        "scan_session_id": "scan-123",
        "store_id": None,
        "prepared_items_hash": HASH,
        "bundle_hash": "sha256:" + "b" * 64,
        "knowledge_revision": "revision-1",
        "items": [
            {
                "item_id": "scan-123:0",
                "source_index": 0,
                "menu_id": None,
                "menu_category_id": None,
                "normalization_status": "unmatched",
                "match_type": None,
                "selection_method": "none",
                "status": "completed",
                "route": "no_information",
                "risk_level": "caution",
                "evidence_basis": "unknown",
                "information_status": "none",
                "risk_ingredients": [],
                "matched_tags": [],
                "evidence_refs": [],
                "message": {"verdict": "확인이 필요해요.", "reason": None, "notes": []},
                "owner_cards": [],
                "warnings": [store_warning()],
                "errors": [],
            }
        ],
        "evidence": [],
        "persistence_commands": [],
        "warnings": [],
        "errors": [],
    }


def test_prepare_request_is_closed_and_has_no_store_id() -> None:
    payload = {
        "schema_version": "3.0",
        "trace_id": TRACE_ID,
        "scan_session_id": "scan-123",
        "image": {
            "source": "camera",
            "storage_key": "scans/menu.jpg",
            "version_id": None,
            "expected_etag": None,
        },
        "locale": "ko",
    }
    request = PrepareRequest.model_validate(payload)
    assert request.image.storage_key == "scans/menu.jpg"

    with pytest.raises(ValidationError):
        PrepareRequest.model_validate({**payload, "store_id": 1})


def test_user_profile_requires_consistent_vegetarian_type() -> None:
    with pytest.raises(ValidationError):
        UserProfile.model_validate(
            {
                "religion_type": None,
                "is_vegetarian": True,
                "vegetarian_type": None,
                "no_alcohol": False,
                "allergies": [],
                "no_spicy": False,
            }
        )


def test_safety_warning_must_force_caution() -> None:
    with pytest.raises(ValidationError):
        NodeWarning.model_validate({**store_warning(), "forces_caution": False})


def test_owner_question_state_enforces_round_progression() -> None:
    with pytest.raises(ValidationError):
        OwnerQuestionState.model_validate(
            {
                "question_id": "question-1",
                "ingredient": {"ingredient_id": 1, "canonical_name": "액젓"},
                "ambiguity_flag": None,
                "constraint_tags": [],
                "answered_round": 1,
                "confirmation_state": "needs_third",
            }
        )


def test_storeless_judge_request_accepts_only_empty_store_slices() -> None:
    request = JudgeRequest.model_validate(base_judge_request())
    assert request.store_id is None

    payload = base_judge_request()
    payload["knowledge_bundle"]["items"][0]["candidate_lookup"] = {
        "status": "ok",
        "candidates": [
            {
                "menu_id": 1001,
                "menu_category_id": 11,
                "canonical_name": "김치찌개",
                "score": 1.0,
                "match_type": "canonical",
            }
        ],
    }
    payload["knowledge_bundle"]["items"][0]["candidate_knowledge"] = [
        {
            "menu_id": 1001,
            "exact_feedback": {
                "status": "ok",
                "exact": {
                    "menu_id": 1001,
                    "confirmations": [],
                    "flag_confirmations": [],
                    "owner_question_states": [],
                },
            },
            "ontology": empty_ontology(),
            "priors": {"status": "not_found", "store": [], "evidence": []},
        }
    ]
    with pytest.raises(ValidationError, match="storeless request"):
        JudgeRequest.model_validate(payload)


def test_judge_request_requires_one_to_one_item_mapping() -> None:
    payload = base_judge_request()
    payload["knowledge_bundle"]["items"][0]["item_id"] = "scan-123:1"
    with pytest.raises(ValidationError, match="one-to-one"):
        JudgeRequest.model_validate(payload)


def test_storeless_judge_response_rejects_safe() -> None:
    response = JudgeResponse.model_validate(base_judge_response())
    assert response.items[0].risk_level == "caution"

    payload = deepcopy(base_judge_response())
    payload["items"][0]["risk_level"] = "safe"
    with pytest.raises(ValidationError, match="cannot be safe"):
        JudgeResponse.model_validate(payload)
