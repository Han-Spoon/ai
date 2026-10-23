"""Cross-envelope Supervisor invariants that need both request and response."""

from __future__ import annotations

from .contracts.external import JudgeRequest, JudgeResponse, OwnerCard
from .hashing import (
    ContractHashMismatch,
    knowledge_bundle_hash,
    validate_judge_request_hash,
)


class SupervisorInvariantError(ValueError):
    pass


def _stored_card(card: OwnerCard) -> bool:
    # no-information menu cards are display-only and intentionally have no command.
    return card.ingredient is not None or card.ambiguity_flag is not None


def validate_judge_response(
    request: JudgeRequest, response: JudgeResponse
) -> None:
    """Validate hashes, immutable identifiers, references, and card-command links."""

    validate_judge_request_hash(request)
    immutable_pairs = {
        "schema_version": (request.schema_version, response.schema_version),
        "trace_id": (request.trace_id, response.trace_id),
        "prepare_id": (request.prepare_id, response.prepare_id),
        "scan_session_id": (request.scan_session_id, response.scan_session_id),
        "store_id": (request.store_id, response.store_id),
        "prepared_items_hash": (
            request.prepared_items_hash,
            response.prepared_items_hash,
        ),
        "knowledge_revision": (
            request.knowledge_revision,
            response.knowledge_revision,
        ),
    }
    changed = [name for name, values in immutable_pairs.items() if values[0] != values[1]]
    if changed:
        raise SupervisorInvariantError(
            f"Judge response changed immutable fields: {', '.join(changed)}"
        )

    expected_bundle_hash = knowledge_bundle_hash(request.knowledge_bundle)
    if response.bundle_hash != expected_bundle_hash:
        raise ContractHashMismatch(
            "bundle_hash_mismatch",
            "Judge response bundle hash does not match the received KnowledgeBundle",
        )

    request_items = {item.item_id: item.source_index for item in request.prepared_items}
    response_items = {item.item_id: item.source_index for item in response.items}
    if request_items != response_items:
        raise SupervisorInvariantError(
            "Judge response items must match prepared_items one-to-one"
        )

    evidence_ids = {item.evidence_id for item in response.evidence}
    for command in response.persistence_commands:
        provenance = command.payload.get("provenance")
        if provenance is not None:
            if not isinstance(provenance, dict):
                raise SupervisorInvariantError("command provenance must be an object")
            refs = provenance.get("evidence_refs", [])
            if not isinstance(refs, list) or any(ref not in evidence_ids for ref in refs):
                raise SupervisorInvariantError(
                    "command provenance evidence_refs must resolve"
                )

    cards = {
        (card.item_id, card.card_id): card
        for item in response.items
        for card in item.owner_cards
    }
    command_links = {
        (command.item_id, command.card_id)
        for command in response.persistence_commands
        if command.action == "create_owner_verification_request"
    }
    if any(link not in cards for link in command_links):
        raise SupervisorInvariantError("owner request command points to a missing card")
    required_links = {
        key for key, card in cards.items() if _stored_card(card)
    }
    if command_links != required_links:
        raise SupervisorInvariantError(
            "stored owner cards and owner request commands must match one-to-one"
        )
