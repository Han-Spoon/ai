"""LangGraph orchestration for Judge validation, fan-out, and collection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from pydantic import ValidationError

from ai_normalization.contracts import (
    CandidateSelectionItem,
    CandidateSelectionRequest,
    CandidateSelectionResponse,
)

from .adapters import NodeAdapter
from .contracts.common import NodeError, NodeWarning
from .contracts.db_update import (
    CreateOwnerVerificationRequestPayload,
    DbOperation,
    DbUpdateRequest,
    DbUpdateResponse,
)
from .contracts.decision import (
    HighRiskReviewRequest,
    HighRiskReviewResponse,
    IngredientChoice,
    ReviewDecision,
    ReviewMenu,
)
from .contracts.external import (
    CandidateKnowledge,
    EvidenceRef,
    ItemResult,
    JudgeRequest,
    JudgeResponse,
    OwnerCard,
    PersistenceCommand,
    ResultOwnerCard,
)
from .hashing import knowledge_bundle_hash, validate_judge_request_hash
from .state import (
    ItemAnalysisOutput,
    ItemReviewCandidate,
    ItemTask,
    ItemTaskKnowledge,
    JudgeState,
    PendingDbOperation,
    RunContext,
    merge_commands,
    merge_evidence,
    merge_signals,
)
from .validation import validate_judge_response


class JudgeGraphInvariantError(ValueError):
    """Judge input, adapter output, or collected references are inconsistent."""


class ItemAnalysisError(Exception):
    """Expected item-local failure that must degrade only the affected item."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class HighRiskReviewError(Exception):
    """Expected high-cost review failure; keep the original decision."""


class ItemAnalyzer(Protocol):
    async def run(
        self, task: ItemTask, context: RunContext
    ) -> ItemAnalysisOutput:
        """Analyze exactly one isolated item task."""
        ...


@dataclass(frozen=True)
class JudgeDependencies:
    candidate_selection: NodeAdapter[
        CandidateSelectionRequest, CandidateSelectionResponse
    ]
    item_analyzer: ItemAnalyzer
    high_risk_review: NodeAdapter[HighRiskReviewRequest, HighRiskReviewResponse]
    db_update: NodeAdapter[DbUpdateRequest, DbUpdateResponse]


def build_judge_graph(dependencies: JudgeDependencies) -> Any:
    """Compile Judge validation → ②-B → item fan-out → collect."""

    def validate_knowledge_node(
        state: JudgeState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        request: JudgeRequest = state["request"]
        _validate_runtime_context(request, runtime.context)
        validate_judge_request_hash(request)
        return {
            "prepared_items": request.prepared_items,
            "prepare_signals": request.prepare_signals,
            "knowledge_bundle": request.knowledge_bundle,
            "user_profile": request.user_profile,
            "locale": request.locale,
            "bundle_hash": knowledge_bundle_hash(request.knowledge_bundle),
            "item_updates": [],
            "evidence": [],
            "pending_operations": [],
            "persistence_commands": [],
            "warnings": list(request.prepare_signals.warnings),
            "errors": [],
        }

    async def select_candidates_node(
        state: JudgeState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        context = runtime.context.node_context("scan", None, include_store=True)
        knowledge_by_item = {
            item.item_id: item for item in state["knowledge_bundle"].items
        }
        items = [
            CandidateSelectionItem(
                **prepared.model_dump(),
                candidate_status=knowledge_by_item[
                    prepared.item_id
                ].candidate_lookup.status,
                match_candidates=knowledge_by_item[
                    prepared.item_id
                ].candidate_lookup.candidates,
            )
            for prepared in state["prepared_items"]
        ]
        request = CandidateSelectionRequest(
            context=context,
            items=items,
            warnings=list(state.get("warnings", [])),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.candidate_selection.run(request)
        _validate_candidate_response(request, response)
        return {
            "candidate_selection_response": response,
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }

    def fan_out_items(
        state: JudgeState, runtime: Runtime[RunContext]
    ) -> list[Send]:
        return [
            Send("analyze_item", {"item_task": task})
            for task in _build_item_tasks(state, runtime.context)
        ]

    async def analyze_item_node(
        state: JudgeState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["item_task"]
        try:
            output = await dependencies.item_analyzer.run(task, runtime.context)
        except ItemAnalysisError as error:
            output = _fallback_item_output(task, error)
        _validate_item_output(task, output, runtime.context)
        return {"item_updates": [output]}

    def collect_items_node(state: JudgeState) -> dict[str, Any]:
        updates = list(state.get("item_updates", []))
        expected = {
            item.item_id: item.source_index for item in state["prepared_items"]
        }
        actual = {
            update.result.item_id: update.result.source_index for update in updates
        }
        if actual != expected:
            raise JudgeGraphInvariantError(
                "item outputs must match prepared_items one-to-one"
            )

        ordered_updates = sorted(
            updates, key=lambda update: update.result.source_index
        )
        evidence: list[EvidenceRef] = []
        commands: list[PersistenceCommand] = []
        operations: list[PendingDbOperation] = []
        warnings: list[NodeWarning] = []
        errors: list[NodeError] = []
        for update in ordered_updates:
            evidence = merge_evidence(evidence, list(update.evidence))
            commands = merge_commands(commands, list(update.commands))
            operations.extend(update.operations)
            warnings = merge_signals(
                warnings, list(update.result.warnings)
            )  # type: ignore[assignment]
            errors = merge_signals(
                errors, list(update.result.errors)
            )  # type: ignore[assignment]

        _validate_collected_references(ordered_updates, evidence)
        return {
            "ordered_items": [update.result for update in ordered_updates],
            "review_candidates": {
                update.result.item_id: update.review
                for update in ordered_updates
                if update.review is not None
            },
            "evidence": evidence,
            "pending_operations": operations,
            "persistence_commands": commands,
            "warnings": warnings,
            "errors": errors,
        }

    async def high_risk_review_node(
        state: JudgeState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        if runtime.context.store_id is None:
            return {}
        selected = _select_review_items(state)
        if not selected:
            return {}

        prepared_by_id = {item.item_id: item for item in state["prepared_items"]}

        async def run_review(item: ItemResult) -> tuple[ItemResult, list[Any], list[Any]]:
            candidate = state["review_candidates"][item.item_id]
            request = _build_high_risk_request(
                item,
                candidate,
                prepared_by_id[item.item_id],
                state,
                runtime.context,
            )
            try:
                response = await dependencies.high_risk_review.run(request)
                _validate_high_risk_response(request, response, candidate)
            except (
                HighRiskReviewError,
                TimeoutError,
                ValidationError,
                JudgeGraphInvariantError,
            ):
                return item, [], []
            return (
                _apply_high_risk_response(item, response),
                list(response.warnings),
                list(response.errors),
            )

        reviewed = await asyncio.gather(*(run_review(item) for item in selected))
        replacements = {item.item_id: item for item, _, _ in reviewed}
        return {
            "ordered_items": [
                replacements.get(item.item_id, item) for item in state["ordered_items"]
            ],
            "warnings": [warning for _, values, _ in reviewed for warning in values],
            "errors": [error for _, _, values in reviewed for error in values],
        }

    async def db_update_node(
        state: JudgeState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        if runtime.context.store_id is None:
            return {}

        pending = list(state.get("pending_operations", []))
        for item in state["ordered_items"]:
            pending.extend(
                PendingDbOperation(
                    item_id=item.item_id,
                    operation=DbOperation(
                        operation_id=f"operation:{item.item_id}:owner-request:{card.card_id}",
                        operation_type="create_owner_verification_request",
                        payload=_owner_request_payload(item, card),
                    ),
                )
                for card in item.owner_cards
                if _stored_card(card)
            )
        pending = _dedupe_pending_operations(pending)
        if not pending:
            return {}

        request = DbUpdateRequest(
            context=runtime.context.node_context(
                "out_of_band", None, include_store=True
            ),
            operations=[item.operation for item in pending],
            warnings=list(state.get("warnings", [])),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.db_update.run(request)
        _validate_db_update_response(request, response, pending)
        scope_by_operation = {
            item.operation.operation_id: item.item_id for item in pending
        }
        return {
            "persistence_commands": [
                PersistenceCommand.model_validate(
                    {
                        **command.model_dump(mode="python"),
                        "item_id": scope_by_operation[command.operation_id],
                    }
                )
                for command in response.commands
            ],
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }

    def finalize_node(state: JudgeState) -> dict[str, Any]:
        request: JudgeRequest = state["request"]
        response = JudgeResponse(
            schema_version=request.schema_version,
            trace_id=request.trace_id,
            prepare_id=request.prepare_id,
            scan_session_id=request.scan_session_id,
            store_id=request.store_id,
            prepared_items_hash=request.prepared_items_hash,
            bundle_hash=state["bundle_hash"],
            knowledge_revision=request.knowledge_revision,
            items=state["ordered_items"],
            evidence=state.get("evidence", []),
            persistence_commands=state.get("persistence_commands", []),
            warnings=state.get("warnings", []),
            errors=state.get("errors", []),
        )
        validate_judge_response(request, response)
        return {"response": response}

    graph = StateGraph(JudgeState, context_schema=RunContext)
    graph.add_node("validate_knowledge", validate_knowledge_node)
    graph.add_node("select_candidates", select_candidates_node)
    graph.add_node("analyze_item", analyze_item_node)
    graph.add_node("collect_items", collect_items_node)
    graph.add_node("high_risk_review", high_risk_review_node)
    graph.add_node("db_update", db_update_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "validate_knowledge")
    graph.add_edge("validate_knowledge", "select_candidates")
    graph.add_conditional_edges("select_candidates", fan_out_items)
    graph.add_edge("analyze_item", "collect_items")
    graph.add_edge("collect_items", "high_risk_review")
    graph.add_edge("high_risk_review", "db_update")
    graph.add_edge("db_update", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=False, name="judge_graph")


_REVIEW_PRIORITY = {
    "anomaly": 0,
    "unknown_tag": 1,
    "no_information": 2,
    "partial_information": 3,
    "insufficient_web_sources": 4,
}


def _select_review_items(state: JudgeState) -> list[ItemResult]:
    candidates = state.get("review_candidates", {})
    return sorted(
        (item for item in state["ordered_items"] if item.item_id in candidates),
        key=lambda item: (
            _REVIEW_PRIORITY[candidates[item.item_id].reason_code],
            item.source_index,
        ),
    )[:3]


def _build_high_risk_request(
    item: ItemResult,
    candidate: ItemReviewCandidate,
    prepared: Any,
    state: JudgeState,
    context: RunContext,
) -> HighRiskReviewRequest:
    return HighRiskReviewRequest(
        context=context.node_context("item", item.item_id, include_store=True),
        menu=ReviewMenu(
            menu_id=item.menu_id,
            normalized_menu_name=prepared.normalized_menu_name,
            raw_menu_name=prepared.raw_menu_name,
            description=prepared.description,
        ),
        reason_code=candidate.reason_code,
        decision=ReviewDecision(
            risk_level=item.risk_level,
            evidence_basis=item.evidence_basis,
            message=item.message,
            owner_cards=[_decision_owner_card(card) for card in item.owner_cards],
        ),
        found_ingredients=list(candidate.found_ingredients),
        ingredient_choices=_ingredient_choices(state, candidate),
        user_profile=state["user_profile"],
        locale=state["locale"],
        warnings=list(item.warnings),
        errors=list(item.errors),
    )


def _ingredient_choices(
    state: JudgeState, candidate: ItemReviewCandidate
) -> list[IngredientChoice]:
    graph = state["knowledge_bundle"].ingredient_graph
    if graph.status != "ok":
        return []
    found_ids = {
        item.ingredient.ingredient_id
        for item in candidate.found_ingredients
        if item.ingredient.ingredient_id is not None
    }
    tags: dict[int, list[str]] = {}
    for tag in graph.tags:
        tags.setdefault(tag.ingredient_id, []).append(tag.tag_code)
    return [
        IngredientChoice(
            ingredient_id=ingredient.ingredient_id,
            canonical_name=ingredient.canonical_name,
            localized_name=(
                ingredient.localized_name or ingredient.canonical_name
            ),
            constraint_tags=list(dict.fromkeys(tags.get(ingredient.ingredient_id, []))),
        )
        for ingredient in graph.ingredients
        if ingredient.ingredient_id not in found_ids
    ]


def _validate_high_risk_response(
    request: HighRiskReviewRequest,
    response: HighRiskReviewResponse,
    candidate: ItemReviewCandidate,
) -> None:
    _validate_response_signals(request, response, "High-risk review")
    choice_keys = {_ingredient_key(item) for item in request.ingredient_choices}
    missed_keys = [_ingredient_key(item) for item in response.missed_ingredients]
    if len(missed_keys) != len(set(missed_keys)) or any(
        key not in choice_keys for key in missed_keys
    ):
        raise JudgeGraphInvariantError(
            "high-risk review selected an ingredient outside ingredient_choices"
        )
    if any(key in candidate.answered_ingredient_keys for key in missed_keys):
        raise JudgeGraphInvariantError(
            "high-risk review selected an already answered ingredient"
        )

    original_ids = {card.card_id for card in request.decision.owner_cards}
    response_ids = [card.card_id for card in response.owner_cards]
    if len(response_ids) != len(set(response_ids)) or not original_ids <= set(response_ids):
        raise JudgeGraphInvariantError(
            "high-risk review must preserve original cards without duplicates"
        )
    missed_set = set(missed_keys)
    for card in response.owner_cards:
        if card.card_id in original_ids:
            continue
        if (
            card.ingredient is None
            or _ingredient_key(card.ingredient) not in missed_set
            or "high_risk_review" not in card.reason_codes
        ):
            raise JudgeGraphInvariantError(
                "new high-risk cards must target a missed ingredient"
            )
        if _ingredient_key(card.ingredient) in candidate.answered_ingredient_keys:
            raise JudgeGraphInvariantError(
                "high-risk review cannot ask an answered ingredient again"
            )


def _apply_high_risk_response(
    item: ItemResult, response: HighRiskReviewResponse
) -> ItemResult:
    cards = [_result_owner_card(card) for card in response.owner_cards]
    owner_status = item.owner_answer_status
    if owner_status == "complete" and any(_stored_card(card) for card in cards):
        owner_status = "partial"
    return ItemResult.model_validate(
        {
            **item.model_dump(mode="python"),
            "owner_answer_status": owner_status,
            "message": item.message.model_copy(update={"reason": response.reason}),
            "owner_cards": cards,
            "warnings": _dedupe_signals([*item.warnings, *response.warnings]),
            "errors": _dedupe_signals([*item.errors, *response.errors]),
        }
    )


def _decision_owner_card(card: ResultOwnerCard) -> OwnerCard:
    return OwnerCard.model_validate(card.model_dump(mode="python"))


def _result_owner_card(card: OwnerCard) -> ResultOwnerCard:
    return ResultOwnerCard.model_validate(card.model_dump(mode="python"))


def _stored_card(card: ResultOwnerCard) -> bool:
    return card.ingredient is not None or card.ambiguity_flag is not None


def _owner_request_payload(
    item: ItemResult, card: ResultOwnerCard
) -> CreateOwnerVerificationRequestPayload:
    probability = None
    if card.ingredient is not None:
        key = _ingredient_key(card.ingredient)
        probability = next(
            (
                ingredient.posterior_mean
                for ingredient in item.risk_ingredients
                if _ingredient_key(ingredient.ingredient) == key
                and ingredient.posterior_mean is not None
            ),
            None,
        )
    return CreateOwnerVerificationRequestPayload(
        item_id=card.item_id,
        card_id=card.card_id,
        menu_id=card.menu_id,
        ingredient=card.ingredient,
        ambiguity_flag=card.ambiguity_flag,
        constraint_tags=card.constraint_tags,
        recipe_probability=probability,
        question=card.question,
        reason_codes=card.reason_codes,
        evidence_refs=card.evidence_refs,
    )


def _dedupe_pending_operations(
    pending: list[PendingDbOperation],
) -> list[PendingDbOperation]:
    """Remove operations that ⑦ would assign the same idempotency key."""

    unique: list[PendingDbOperation] = []
    seen: set[tuple[Any, ...]] = set()
    for item in pending:
        operation = item.operation
        payload = operation.payload
        if operation.operation_type == "create_review_item":
            if payload.review_origin == "web_search":
                menu_key: tuple[Any, ...] = (
                    payload.menu.normalized_menu_name,
                    tuple(sorted(source.source_url for source in payload.web_sources)),
                )
            else:
                suggestion = payload.variant_suggestion
                if suggestion is None:
                    raise JudgeGraphInvariantError(
                        "variant review operation omitted its suggestion"
                    )
                menu_key = (suggestion.base_menu_id, suggestion.remain_token, ())
            key = (operation.operation_type, payload.review_origin, *menu_key)
        elif operation.operation_type == "save_web_search_cache":
            key = (operation.operation_type, payload.normalized_menu_name)
        elif operation.operation_type == "create_owner_verification_request":
            key = (
                operation.operation_type,
                payload.item_id,
                payload.card_id,
                payload.menu_id,
            )
        else:
            key = (
                operation.operation_type,
                operation.payload.model_dump_json(),
            )
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return unique


def _validate_db_update_response(
    request: DbUpdateRequest,
    response: DbUpdateResponse,
    pending: list[PendingDbOperation],
) -> None:
    _validate_response_signals(request, response, "DB update")
    expected = {
        operation.operation_id: operation.operation_type
        for operation in request.operations
    }
    actual = {
        command.operation_id: command.action for command in response.commands
    }
    if len(response.commands) != len(request.operations) or actual != expected:
        raise JudgeGraphInvariantError(
            "DB update must return one matching command per operation"
        )
    if any(command.item_id is not None for command in response.commands):
        raise JudgeGraphInvariantError("batched DB commands changed scan scope")

    operations = {
        item.operation.operation_id: item.operation for item in pending
    }
    for command in response.commands:
        operation = operations[command.operation_id]
        payload = operation.payload
        source_payload = payload.model_dump(mode="json")
        command_payload = command.payload.model_dump(
            mode="json", exclude={"provenance"}
        )
        if command_payload != source_payload:
            raise JudgeGraphInvariantError("DB update changed operation payload")

        provenance = command.payload.provenance
        if operation.operation_type == "create_review_item":
            if payload.review_origin == "web_search":
                origin = source_type = "web_search"
                refs = [
                    source.evidence.evidence_id for source in payload.web_sources
                ]
            else:
                suggestion = payload.variant_suggestion
                if suggestion is None:
                    raise JudgeGraphInvariantError(
                        "variant review operation omitted its suggestion"
                    )
                origin, source_type = "ontology", "ocr_variant_tag"
                refs = [suggestion.evidence.evidence_id]
            evidence_class = "soft"
        elif operation.operation_type == "save_web_search_cache":
            origin = source_type = "web_search"
            evidence_class = "soft"
            refs = [source.evidence.evidence_id for source in payload.sources]
        elif operation.operation_type == "create_owner_verification_request":
            origin, source_type = "decision", "decision_policy"
            evidence_class = None
            refs = list(payload.evidence_refs)
            if command.card_id != payload.card_id:
                raise JudgeGraphInvariantError("DB update changed owner card scope")
        else:
            raise JudgeGraphInvariantError(
                "Judge received an unsupported batched DB operation"
            )
        if (
            provenance.origin_node != origin
            or provenance.source_type != source_type
            or provenance.evidence_class != evidence_class
            or provenance.evidence_refs != refs
        ):
            raise JudgeGraphInvariantError("DB update changed operation provenance")


def _validate_response_signals(request: Any, response: Any, node: str) -> None:
    if response.context != request.context:
        raise JudgeGraphInvariantError(f"{node} changed immutable item context")
    for field in ("warnings", "errors"):
        required = {
            (signal.code, signal.node, signal.item_id)
            for signal in getattr(request, field)
        }
        actual = {
            (signal.code, signal.node, signal.item_id)
            for signal in getattr(response, field)
        }
        if not required <= actual:
            raise JudgeGraphInvariantError(f"{node} dropped input {field}")


def _ingredient_key(ingredient: Any) -> tuple[str, int | str]:
    if ingredient.ingredient_id is not None:
        return ("id", ingredient.ingredient_id)
    return ("name", ingredient.canonical_name)


def _validate_runtime_context(request: JudgeRequest, context: RunContext) -> None:
    pairs = {
        "schema_version": (request.schema_version, context.schema_version),
        "trace_id": (request.trace_id, context.trace_id),
        "scan_session_id": (request.scan_session_id, context.scan_session_id),
        "store_id": (request.store_id, context.store_id),
    }
    changed = [name for name, values in pairs.items() if values[0] != values[1]]
    if changed:
        raise JudgeGraphInvariantError(
            f"Judge runtime context mismatch: {', '.join(changed)}"
        )


def _validate_candidate_response(
    request: CandidateSelectionRequest, response: CandidateSelectionResponse
) -> None:
    if response.context != request.context:
        raise JudgeGraphInvariantError("Normalization changed Judge context")
    request_keys = [(item.item_id, item.source_index) for item in request.items]
    response_keys = [(item.item_id, item.source_index) for item in response.items]
    if response_keys != request_keys:
        raise JudgeGraphInvariantError(
            "Candidate selection must preserve every item and source order"
        )
    pass_through = set(CandidateSelectionItem.model_fields)
    for source, selected in zip(request.items, response.items):
        if source.model_dump() != selected.model_dump(include=pass_through):
            raise JudgeGraphInvariantError(
                f"Candidate selection changed input fields for {source.item_id}"
            )


def _build_item_tasks(state: JudgeState, context: RunContext) -> list[ItemTask]:
    response: CandidateSelectionResponse = state["candidate_selection_response"]
    prepared_by_id = {item.item_id: item for item in state["prepared_items"]}
    knowledge_by_id = {
        item.item_id: item for item in state["knowledge_bundle"].items
    }
    tasks: list[ItemTask] = []
    for selection in response.items:
        knowledge = knowledge_by_id[selection.item_id]
        selected_knowledge: CandidateKnowledge | None = None
        if selection.selected_candidate is not None:
            selected_knowledge = next(
                (
                    item
                    for item in knowledge.candidate_knowledge
                    if item.menu_id == selection.selected_candidate.menu_id
                ),
                None,
            )
            if selected_knowledge is None:
                raise JudgeGraphInvariantError(
                    "selected candidate has no matching knowledge slice"
                )

        warnings = [
            warning
            for warning in (
                list(state["prepare_signals"].warnings) + list(response.warnings)
            )
            if warning.item_id == selection.item_id
        ]
        if context.store_id is None:
            warnings.append(_store_context_warning(selection.item_id))
        errors = [
            error
            for error in response.errors
            if error.item_id == selection.item_id
        ]
        tasks.append(
            ItemTask(
                prepared_item=prepared_by_id[selection.item_id],
                selection=selection,
                knowledge=ItemTaskKnowledge(
                    item_id=selection.item_id,
                    selected=selected_knowledge,
                    fallback_ontology=knowledge.fallback_ontology,
                    web_search_cache=knowledge.web_search_cache,
                ),
                ingredient_graph=state["knowledge_bundle"].ingredient_graph,
                source_reliability=state[
                    "knowledge_bundle"
                ].source_reliability,
                user_profile=state["user_profile"],
                locale=state["locale"],
                warnings=tuple(_dedupe_signals(warnings)),
                errors=tuple(_dedupe_signals(errors)),
            )
        )
    return tasks


def _validate_item_output(
    task: ItemTask, output: ItemAnalysisOutput, context: RunContext
) -> None:
    result = output.result
    selection = task.selection
    selected = selection.selected_candidate
    expected = {
        "item_id": task.prepared_item.item_id,
        "source_index": task.prepared_item.source_index,
        "menu_id": selected.menu_id if selected else None,
        "menu_category_id": selected.menu_category_id if selected else None,
        "normalization_status": selection.normalization_status,
        "match_type": selected.match_type if selected else None,
        "selection_method": selection.selection_method,
    }
    changed = [name for name, value in expected.items() if getattr(result, name) != value]
    if changed:
        raise JudgeGraphInvariantError(
            f"item output changed task fields: {', '.join(changed)}"
        )
    required_warnings = {
        (warning.code, warning.node, warning.item_id) for warning in task.warnings
    }
    actual_warnings = {
        (warning.code, warning.node, warning.item_id) for warning in result.warnings
    }
    if not required_warnings <= actual_warnings:
        raise JudgeGraphInvariantError("item output dropped required warnings")
    if result.risk_level == "safe" and any(
        warning.forces_caution for warning in task.warnings
    ):
        raise JudgeGraphInvariantError(
            "item output cannot be safe while a safety warning is active"
        )
    if result.risk_level == "safe" and result.owner_answer_status != "complete":
        raise JudgeGraphInvariantError(
            "item output cannot be safe without complete owner answers"
        )
    if context.store_id is None and (
        result.risk_level == "safe"
        or result.owner_answer_status != "none"
        or result.owner_cards
        or output.commands
    ):
        raise JudgeGraphInvariantError(
            "storeless item output cannot be safe or create owner/store output"
        )


def _validate_collected_references(
    updates: list[ItemAnalysisOutput], evidence: list[EvidenceRef]
) -> None:
    evidence_ids = {item.evidence_id for item in evidence}
    refs = [
        ref
        for update in updates
        for ref in (
            update.result.evidence_refs
            + [
                ref
                for ingredient in update.result.risk_ingredients
                for ref in ingredient.evidence_refs
            ]
        )
    ]
    if any(ref not in evidence_ids for ref in refs):
        raise JudgeGraphInvariantError("collected evidence_refs must resolve")
    for update in updates:
        for command in update.commands:
            provenance = command.payload.get("provenance")
            if provenance is None:
                continue
            command_refs = provenance.get("evidence_refs")
            if not isinstance(command_refs, list) or any(
                ref not in evidence_ids for ref in command_refs
            ):
                raise JudgeGraphInvariantError(
                    "collected command evidence_refs must resolve"
                )


def _fallback_item_output(
    task: ItemTask, error: ItemAnalysisError
) -> ItemAnalysisOutput:
    selected = task.selection.selected_candidate
    node_error = NodeError(
        code=error.code,
        node="item_graph",
        item_id=task.prepared_item.item_id,
        message=str(error),
        retryable=error.retryable,
        fallback="caution",
    )
    return ItemAnalysisOutput(
        result=ItemResult(
            item_id=task.prepared_item.item_id,
            source_index=task.prepared_item.source_index,
            menu_id=selected.menu_id if selected else None,
            menu_category_id=selected.menu_category_id if selected else None,
            normalization_status=task.selection.normalization_status,
            match_type=selected.match_type if selected else None,
            selection_method=task.selection.selection_method,
            status="partial_failed",
            route="no_information",
            risk_level="caution",
            evidence_basis="unknown",
            information_status="partial",
            owner_answer_status="none",
            risk_ingredients=[],
            matched_tags=[],
            evidence_refs=[],
            message={
                "verdict": "확인하지 못했어요.",
                "reason": None,
                "notes": [],
            },
            owner_cards=[],
            warnings=list(task.warnings),
            errors=[*task.errors, node_error],
        )
    )


def _store_context_warning(item_id: str) -> NodeWarning:
    return NodeWarning(
        code="store_context_missing",
        node="supervisor",
        item_id=item_id,
        message="가게 정보가 없어 확정 정보와 저장 기능을 사용하지 않습니다.",
        forces_caution=True,
    )


def _dedupe_signals(signals: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[tuple[str, str, str | None]] = set()
    for signal in signals:
        key = (signal.code, signal.node, signal.item_id)
        if key not in seen:
            result.append(signal)
            seen.add(key)
    return result
