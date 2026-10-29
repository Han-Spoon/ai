"""⑦ DB Update Tool: turn Supervisor operations into storage commands.

docs/7-tool-dbupdate.md §1-2, §3, ⓪ §4-6. ⑦ only builds commands; the backend checks
permission, FK and idempotency and does the physical write (AGENTS.md).
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from ai_supervisor.contracts.db_update import (
    CreateOwnerVerificationRequestPayload,
    CreateReviewItemPayload,
    DbCommand,
    DbOperation,
    DbUpdateRequest,
    DbUpdateResponse,
    Provenance,
    RecordOwnerAnswerPayload,
    SaveWebSearchCachePayload,
    UpdateSourceReliabilityPayload,
)


def idempotency_key(action: str, content: dict[str, Any]) -> str:
    """``sha256:`` + sha256(action|canonical JSON). Content only: no trace_id,
    operation_id or time, so a retried Judge yields the same key (⓪ §4-6)."""

    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + sha256(f"{action}|{canonical}".encode()).hexdigest()


def _review(payload: CreateReviewItemPayload, store_id: int) -> tuple[dict, Provenance]:
    if payload.review_origin == "web_search":
        menu: dict[str, Any] = {"normalized_menu_name": payload.menu.normalized_menu_name}
        refs = [source.evidence.evidence_id for source in payload.web_sources]
        provenance = Provenance(
            origin_node="web_search", source_type="web_search", evidence_class="soft", evidence_refs=refs
        )
    else:
        suggestion = payload.variant_suggestion
        menu = {"base_menu_id": suggestion.base_menu_id, "remain_token": suggestion.remain_token}
        provenance = Provenance(
            origin_node="ontology",
            source_type="ocr_variant_tag",
            evidence_class="soft",
            evidence_refs=[suggestion.evidence.evidence_id],
        )
    content = {
        "store_id": store_id,
        "review_origin": payload.review_origin,
        "menu": menu,
        "source_urls": sorted(source.source_url for source in payload.web_sources),
    }
    return content, provenance


def _owner_request(
    payload: CreateOwnerVerificationRequestPayload, context
) -> tuple[dict, Provenance]:
    content: dict[str, Any] = {
        "store_id": context.store_id,
        "scan_session_id": context.scan_session_id,
        "item_id": payload.item_id,
        "card_id": payload.card_id,
        "menu_id": payload.menu_id,
    }
    if payload.ingredient is not None:
        content["ingredient_id"] = payload.ingredient.ingredient_id
    else:
        content["ambiguity_flag"] = payload.ambiguity_flag
        content["constraint_tags"] = sorted(payload.constraint_tags)
    provenance = Provenance(
        origin_node="decision",
        source_type="decision_policy",
        evidence_class=None,  # no answer yet
        evidence_refs=list(payload.evidence_refs),
    )
    return content, provenance


def _owner_answer(payload: RecordOwnerAnswerPayload) -> tuple[dict, Provenance]:
    # one answer per question; the backend confirms it at once (⑦ §2-3)
    provenance = Provenance(
        origin_node="external_owner_answer",
        source_type="owner_feedback",
        evidence_class="hard",
        evidence_refs=[],
    )
    return {"question_id": payload.question_id}, provenance


def _reliability(payload: UpdateSourceReliabilityPayload) -> tuple[dict, Provenance]:
    provenance = Provenance(
        origin_node="db_update",
        source_type="system",
        evidence_class=None,
        evidence_refs=[payload.trigger_evidence_ref],
    )
    content = {"source_type": payload.source_type, "trigger_evidence_ref": payload.trigger_evidence_ref}
    return content, provenance


def _web_cache(payload: SaveWebSearchCachePayload) -> tuple[dict, Provenance]:
    provenance = Provenance(
        origin_node="web_search",
        source_type="web_search",
        evidence_class="soft",
        evidence_refs=[source.evidence.evidence_id for source in payload.sources],
    )
    # No search rank any more (10/27): the new source URLs tell one save from the next.
    # A menu-name-only key would make the backend drop every later save as a repeat.
    content = {
        "normalized_menu_name": payload.normalized_menu_name,
        "source_urls": sorted(source.source_url for source in payload.sources),
    }
    return content, provenance


def build_command(operation: DbOperation, request: DbUpdateRequest) -> DbCommand:
    context = request.context
    payload = operation.payload
    action = operation.operation_type
    if action == "create_review_item":
        content, provenance = _review(payload, context.store_id)
    elif action == "create_owner_verification_request":
        content, provenance = _owner_request(payload, context)
    elif action == "record_owner_answer":
        content, provenance = _owner_answer(payload)
    elif action == "update_source_reliability":
        content, provenance = _reliability(payload)
    else:
        content, provenance = _web_cache(payload)

    return DbCommand.model_validate(
        {
            "command_id": f"command:{operation.operation_id}",
            "operation_id": operation.operation_id,
            "item_id": context.item_id,
            "card_id": payload.card_id if action == "create_owner_verification_request" else None,
            "action": action,
            "idempotency_key": idempotency_key(action, content),
            # every input field kept, provenance added (⓪ §4-6)
            "payload": {**payload.model_dump(mode="json"), "provenance": provenance.model_dump(mode="json")},
        }
    )


def build_commands(request: DbUpdateRequest) -> DbUpdateResponse:
    return DbUpdateResponse(
        context=request.context,
        commands=[build_command(operation, request) for operation in request.operations],
        warnings=list(request.warnings),
        errors=list(request.errors),
    )
