"""LangGraph orchestration for Judge validation, fan-out, and collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

from ai_normalization.contracts import (
    CandidateSelectionItem,
    CandidateSelectionRequest,
    CandidateSelectionResponse,
)

from .adapters import NodeAdapter
from .contracts.common import NodeError, NodeWarning
from .contracts.external import (
    CandidateKnowledge,
    EvidenceRef,
    ItemResult,
    JudgeRequest,
    PersistenceCommand,
)
from .hashing import knowledge_bundle_hash, validate_judge_request_hash
from .state import (
    ItemAnalysisOutput,
    ItemTask,
    ItemTaskKnowledge,
    JudgeState,
    RunContext,
    merge_commands,
    merge_evidence,
    merge_signals,
)


class JudgeGraphInvariantError(ValueError):
    """Judge input, adapter output, or collected references are inconsistent."""


class ItemAnalysisError(Exception):
    """Expected item-local failure that must degrade only the affected item."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


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
        warnings: list[NodeWarning] = []
        errors: list[NodeError] = []
        for update in ordered_updates:
            evidence = merge_evidence(evidence, list(update.evidence))
            commands = merge_commands(commands, list(update.commands))
            warnings = merge_signals(
                warnings, list(update.result.warnings)
            )  # type: ignore[assignment]
            errors = merge_signals(
                errors, list(update.result.errors)
            )  # type: ignore[assignment]

        _validate_collected_references(ordered_updates, evidence)
        return {
            "ordered_items": [update.result for update in ordered_updates],
            "evidence": evidence,
            "persistence_commands": commands,
            "warnings": warnings,
            "errors": errors,
        }

    graph = StateGraph(JudgeState, context_schema=RunContext)
    graph.add_node("validate_knowledge", validate_knowledge_node)
    graph.add_node("select_candidates", select_candidates_node)
    graph.add_node("analyze_item", analyze_item_node)
    graph.add_node("collect_items", collect_items_node)
    graph.add_edge(START, "validate_knowledge")
    graph.add_edge("validate_knowledge", "select_candidates")
    graph.add_conditional_edges("select_candidates", fan_out_items)
    graph.add_edge("analyze_item", "collect_items")
    graph.add_edge("collect_items", END)
    return graph.compile(checkpointer=False, name="judge_graph")


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
    if context.store_id is None and (
        result.risk_level == "safe" or result.owner_cards or output.commands
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
