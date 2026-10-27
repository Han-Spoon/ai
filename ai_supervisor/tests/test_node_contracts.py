from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from ai_supervisor.contracts import (
    BayesianRequest,
    DbUpdateRequest,
    DecisionRequest,
    ExactFeedbackRequest,
    IngredientMappingRequest,
    OcrRequest,
    OntologyRequest,
    TagLookupRequest,
    WebAggregateRequest,
    WebCollectRequest,
    WebCollectResponse,
)

TRACE_ID = "0199a2f0-0000-7000-8000-000000000001"


def item_context(store_id=123) -> dict:
    return {
        "schema_version": "3.0",
        "trace_id": TRACE_ID,
        "scan_session_id": "scan-123",
        "store_id": store_id,
        "call_scope": "item",
        "item_id": "scan-123:0",
    }


def scan_context() -> dict:
    return {
        "schema_version": "3.0",
        "trace_id": TRACE_ID,
        "scan_session_id": "scan-123",
        "call_scope": "scan",
        "item_id": None,
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


def test_ocr_contract_is_closed_and_prepare_scoped() -> None:
    payload = {
        "context": scan_context(),
        "image": {
            "source": "camera",
            "storage_key": "scans/menu.jpg",
            "image_url": None,
            "version_id": None,
            "expected_etag": None,
        },
        "warnings": [],
        "errors": [],
    }
    assert OcrRequest.model_validate(payload).image.storage_key == "scans/menu.jpg"

    payload["context"]["store_id"] = None
    with pytest.raises(ValidationError, match="must not contain store_id"):
        OcrRequest.model_validate(payload)


def test_exact_contract_requires_store_and_exact_scope() -> None:
    payload = {
        "context": item_context(),
        "menu": {
            "menu_id": 1001,
            "menu_category_id": 11,
            "normalized_menu_name": "김치찌개",
        },
        "scope_hint": "exact",
        "warnings": [],
        "errors": [],
    }
    assert ExactFeedbackRequest.model_validate(payload).scope_hint == "exact"

    payload["context"]["store_id"] = None
    with pytest.raises(ValidationError, match="positive store_id"):
        ExactFeedbackRequest.model_validate(payload)


def test_ontology_knowledge_accepts_only_raw_bundle_columns() -> None:
    payload = {
        "context": item_context(),
        "menu": {
            "menu_id": 1001,
            "normalized_menu_name": "김치찌개",
            "match_candidates": [
                {
                    "menu_id": 1001,
                    "menu_category_id": 11,
                    "canonical_name": "김치찌개",
                    "score": 1.0,
                    "match_type": "canonical",
                }
            ],
            "residual_tokens": [],
        },
        "unconfirmed_only": True,
        "confirmed_ingredients": [],
        "anomaly_locked_ingredients": [],
        "knowledge": {
            "status": "ok",
            "menu": {
                "menu_id": 1001,
                "base_menu_id": None,
                "remain_token": None,
                "menu_category_id": 11,
                "source": "curated",
                "ambiguity_flags": [],
                "knowledge_revision": 7,
            },
            "recipe_ingredients": [],
            "menu_recipe_corpora": [],
            "menu_ingredient_priors": [],
            "cache": None,
        },
        "ingredient_graph": {
            "status": "not_found",
            "ingredients": [],
            "aliases": [],
            "tags": [],
            "compositions": [],
            "composition_tags": [],
        },
        "warnings": [],
        "errors": [],
    }
    assert OntologyRequest.model_validate(payload).knowledge.menu.source == "curated"

    invalid = deepcopy(payload)
    invalid["knowledge"]["menu"]["exists_in_db"] = True
    with pytest.raises(ValidationError):
        OntologyRequest.model_validate(invalid)


def test_tag_lookup_contract_is_item_scoped_and_rejects_duplicate_ids() -> None:
    payload = {
        "context": item_context(),
        "ingredient_ids": [101, 102],
        "ingredient_graph": {
            "status": "not_found",
            "ingredients": [],
            "aliases": [],
            "tags": [],
            "compositions": [],
            "composition_tags": [],
        },
        "warnings": [],
        "errors": [],
    }
    assert TagLookupRequest.model_validate(payload).ingredient_ids == [101, 102]

    payload["ingredient_ids"] = [101, 101]
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        TagLookupRequest.model_validate(payload)


def test_ingredient_mapping_contract_rejects_duplicate_names() -> None:
    payload = {
        "context": item_context(None),
        "ingredient_names": ["진간장", "소고기"],
        "ingredient_graph": {
            "status": "not_found",
            "ingredients": [],
            "aliases": [],
            "tags": [],
            "compositions": [],
            "composition_tags": [],
        },
        "warnings": [],
        "errors": [],
    }
    assert IngredientMappingRequest.model_validate(payload).ingredient_names == [
        "진간장",
        "소고기",
    ]

    payload["ingredient_names"] = ["진간장", "진간장"]
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        IngredientMappingRequest.model_validate(payload)


def test_bayesian_contract_rejects_removed_cluster_and_global_priors() -> None:
    payload = {
        "context": item_context(),
        "menu_category_id": None,
        "ingredients": [],
        "priors": {
            "status": "not_found",
            "store": [],
            "cluster": [],
            "global": [],
            "evidence": [],
        },
        "source_reliability": {"status": "not_found", "weights": []},
        "warnings": [],
        "errors": [],
    }
    assert BayesianRequest.model_validate(payload).priors.global_ == []

    payload["priors"]["global"] = [
        {"ingredient_id": 1, "alpha": 1.0, "beta": 1.0, "evidence_refs": []}
    ]
    with pytest.raises(ValidationError, match="must remain empty"):
        BayesianRequest.model_validate(payload)


def test_web_collect_uses_documented_source_limit() -> None:
    payload = {
        "context": item_context(None),
        "menu": {"menu_id": None, "normalized_menu_name": "마라탕", "locale_hint": "ko"},
        "max_sources": 10,
        "cached_sources": [],
        "next_rank": 1,
        "warnings": [],
        "errors": [],
    }
    assert WebCollectRequest.model_validate(payload).max_sources == 10
    payload["max_sources"] = 9
    with pytest.raises(ValidationError):
        WebCollectRequest.model_validate(payload)


def test_web_collect_response_reports_next_rank() -> None:
    payload = {
        "context": item_context(None),
        "menu": {"menu_id": None, "normalized_menu_name": "마라탕"},
        "found": False,
        "sources": [],
        "next_rank": 14,
        "warnings": [],
        "errors": [],
    }
    assert WebCollectResponse.model_validate(payload).next_rank == 14
    del payload["next_rank"]
    with pytest.raises(ValidationError, match="next_rank"):
        WebCollectResponse.model_validate(payload)


def test_web_aggregate_sources_carry_title() -> None:
    source = {
        "source_url": "https://www.10000recipe.com/recipe/1",
        "title": "김치찜 황금레시피",
        "source_origin": "new",
        "corpus": "10000recipe",
        "evidence_id": "web:https://www.10000recipe.com/recipe/1",
        "ingredients": [],
    }
    payload = {
        "context": item_context(),
        "menu": {"menu_id": None, "normalized_menu_name": "김치찌개"},
        "mapped_sources": [source],
        "warnings": [],
        "errors": [],
    }
    request = WebAggregateRequest.model_validate(payload)
    assert request.mapped_sources[0].title == "김치찜 황금레시피"
    del source["title"]
    with pytest.raises(ValidationError, match="title"):
        WebAggregateRequest.model_validate(payload)


def test_db_update_operation_type_must_match_closed_payload() -> None:
    payload = {
        "context": item_context(),
        "operations": [
            {
                "operation_id": "operation-1",
                "operation_type": "update_source_reliability",
                "payload": {
                    "source_type": "web_search",
                    "correct_count_delta": 1,
                    "comparison_count_delta": 1,
                    "trigger_evidence_ref": "evidence-1",
                },
            }
        ],
        "warnings": [],
        "errors": [],
    }
    assert DbUpdateRequest.model_validate(payload).operations[0].operation_id == "operation-1"

    payload["operations"][0]["operation_type"] = "record_owner_answer"
    with pytest.raises(ValidationError):
        DbUpdateRequest.model_validate(payload)


def test_storeless_decision_requires_warning_and_disables_owner_interaction() -> None:
    payload = {
        "context": item_context(None),
        "menu": {
            "menu_id": None,
            "menu_category_id": None,
            "normalized_menu_name": "마라탕",
        },
        "confirmed_results": [],
        "probability_results": [],
        "proposed_variant_ingredients": [],
        "risk_tag_observations": [],
        "menu_ambiguity_flags": [],
        "flag_confirmations": [],
        "no_information": True,
        "information_status": "none",
        "owner_interaction_allowed": False,
        "user_profile": {
            "religion_type": None,
            "is_vegetarian": False,
            "vegetarian_type": None,
            "no_alcohol": False,
            "allergies": [],
            "no_spicy": False,
        },
        "locale": "ko",
        "localized_names": [],
        "warnings": [
            {
                "code": "store_context_missing",
                "node": "supervisor",
                "item_id": "scan-123:0",
                "message": "가게 문맥이 없습니다.",
                "forces_caution": True,
            }
        ],
        "errors": [],
    }
    assert DecisionRequest.model_validate(payload).owner_interaction_allowed is False

    payload["owner_interaction_allowed"] = True
    with pytest.raises(ValidationError, match="must match store_id"):
        DecisionRequest.model_validate(payload)
