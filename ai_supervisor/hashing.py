"""RFC 8785 hashes used to bind Prepare and Judge payloads."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import rfc8785
from pydantic import BaseModel

from .contracts.external import (
    JudgeRequest,
    KnowledgeBundle,
    PrepareResponse,
    PrepareSignals,
    PreparedItem,
)


class ContractHashMismatch(ValueError):
    """A caller changed data protected by a Supervisor contract hash."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def canonical_sha256(value: Any) -> str:
    """Return ``sha256:<hex>`` over RFC 8785 canonical JSON bytes."""

    canonical = rfc8785.dumps(_json_value(value))
    return f"sha256:{sha256(canonical).hexdigest()}"


def prepared_items_hash(
    items: list[PreparedItem], prepare_signals: PrepareSignals
) -> str:
    return canonical_sha256(
        {
            "prepared_items": items,
            "prepare_signals": prepare_signals,
        }
    )


def knowledge_bundle_hash(bundle: KnowledgeBundle) -> str:
    return canonical_sha256(bundle)


def validate_prepare_response_hash(response: PrepareResponse) -> None:
    expected = prepared_items_hash(response.prepared_items, response.prepare_signals)
    if response.prepared_items_hash != expected:
        raise ContractHashMismatch(
            "prepared_items_hash_mismatch",
            "Prepare response hash does not match prepared items and safety signals",
        )


def validate_judge_request_hash(request: JudgeRequest) -> None:
    expected = prepared_items_hash(request.prepared_items, request.prepare_signals)
    if request.prepared_items_hash != expected:
        raise ContractHashMismatch(
            "prepared_items_hash_mismatch",
            "Judge request changed Prepare items or safety signals",
        )
