from copy import deepcopy

import pytest

from ai_supervisor.contracts import JudgeRequest, JudgeResponse
from ai_supervisor.hashing import (
    ContractHashMismatch,
    canonical_sha256,
    knowledge_bundle_hash,
    prepared_items_hash,
    validate_judge_request_hash,
)
from ai_supervisor.tests.test_contracts import base_judge_request, base_judge_response
from ai_supervisor.validation import validate_judge_response


def test_canonical_hash_is_independent_of_object_key_order() -> None:
    left = {"메뉴": "김치찌개", "score": 1.0, "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "score": 1.0, "메뉴": "김치찌개"}
    assert canonical_sha256(left) == canonical_sha256(right)
    assert canonical_sha256(left).startswith("sha256:")


def valid_judge_pair() -> tuple[JudgeRequest, JudgeResponse]:
    request_payload = base_judge_request()
    request = JudgeRequest.model_validate(request_payload)
    protected_hash = prepared_items_hash(request.prepared_items, request.prepare_signals)
    request_payload["prepared_items_hash"] = protected_hash
    request = JudgeRequest.model_validate(request_payload)

    response_payload = base_judge_response()
    response_payload["prepared_items_hash"] = protected_hash
    response_payload["bundle_hash"] = knowledge_bundle_hash(request.knowledge_bundle)
    response = JudgeResponse.model_validate(response_payload)
    return request, response


def test_judge_hash_and_cross_envelope_invariants_pass_for_matching_pair() -> None:
    request, response = valid_judge_pair()
    validate_judge_request_hash(request)
    validate_judge_response(request, response)


def test_judge_hash_rejects_changed_prepare_data() -> None:
    payload = base_judge_request()
    request = JudgeRequest.model_validate(payload)
    payload["prepared_items_hash"] = prepared_items_hash(
        request.prepared_items, request.prepare_signals
    )
    payload["prepared_items"][0]["display_name"] = "변경된 메뉴명"
    changed = JudgeRequest.model_validate(payload)

    with pytest.raises(ContractHashMismatch) as error:
        validate_judge_request_hash(changed)
    assert error.value.code == "prepared_items_hash_mismatch"


def test_response_rejects_wrong_bundle_hash() -> None:
    request, response = valid_judge_pair()
    payload = response.model_dump(mode="json")
    payload["bundle_hash"] = "sha256:" + "f" * 64
    changed = JudgeResponse.model_validate(payload)
    with pytest.raises(ContractHashMismatch) as error:
        validate_judge_response(request, changed)
    assert error.value.code == "bundle_hash_mismatch"
