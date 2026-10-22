"""LangGraph state types and deterministic reducers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

from .contracts.common import Locale, NodeContext, NodeError, NodeWarning, PositiveInt
from .contracts.external import (
    EvidenceRef,
    ImageRef,
    ItemKnowledge,
    ItemResult,
    KnowledgeBundle,
    PersistenceCommand,
    PrepareSignals,
    PreparedItem,
    ScanQuality,
    SourceReliability,
    UserProfile,
)


def merge_signals(
    current: list[NodeWarning] | list[NodeError],
    update: list[NodeWarning] | list[NodeError],
) -> list[NodeWarning] | list[NodeError]:
    """Append signals once using the canonical (code, node, item_id) key."""

    merged = list(current)
    seen = {(signal.code, signal.node, signal.item_id) for signal in current}
    for signal in update:
        key = (signal.code, signal.node, signal.item_id)
        if key not in seen:
            merged.append(signal)
            seen.add(key)
    return merged


def merge_evidence(current: list[EvidenceRef], update: list[EvidenceRef]) -> list[EvidenceRef]:
    """Merge evidence by ID and reject contradictory duplicate definitions."""

    merged = list(current)
    by_id = {evidence.evidence_id: evidence for evidence in current}
    for evidence in update:
        previous = by_id.get(evidence.evidence_id)
        if previous is not None and previous != evidence:
            raise ValueError(f"conflicting evidence_id: {evidence.evidence_id}")
        if previous is None:
            merged.append(evidence)
            by_id[evidence.evidence_id] = evidence
    return merged


def merge_commands(
    current: list[PersistenceCommand], update: list[PersistenceCommand]
) -> list[PersistenceCommand]:
    """Merge commands by deterministic idempotency key."""

    merged = list(current)
    by_key = {(command.action, command.idempotency_key): command for command in current}
    for command in update:
        key = (command.action, command.idempotency_key)
        previous = by_key.get(key)
        if previous is not None and previous != command:
            raise ValueError(f"conflicting persistence command: {key}")
        if previous is None:
            merged.append(command)
            by_key[key] = command
    return merged


def merge_item_results(current: list[ItemResult], update: list[ItemResult]) -> list[ItemResult]:
    """Collect one immutable final result per item."""

    merged = {item.item_id: item for item in current}
    for item in update:
        previous = merged.get(item.item_id)
        if previous is not None and previous != item:
            raise ValueError(f"conflicting item result: {item.item_id}")
        merged[item.item_id] = item
    return sorted(merged.values(), key=lambda item: item.source_index)


@dataclass(frozen=True)
class RunContext:
    schema_version: Literal["3.0"]
    trace_id: UUID
    scan_session_id: str
    store_id: PositiveInt | None = None

    def __post_init__(self) -> None:
        if self.schema_version != "3.0":
            raise ValueError("unsupported schema_version")
        if not self.scan_session_id:
            raise ValueError("scan_session_id must not be empty")
        if self.store_id is not None and (
            isinstance(self.store_id, bool) or self.store_id <= 0
        ):
            raise ValueError("store_id must be a positive integer or null")

    def node_context(
        self, call_scope: Literal["scan", "item", "out_of_band"], item_id: str | None
    ) -> NodeContext:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "scan_session_id": self.scan_session_id,
            "call_scope": call_scope,
            "item_id": item_id,
        }
        if self.store_id is not None:
            data["store_id"] = self.store_id
        return NodeContext.model_validate(data)


class PrepareState(TypedDict, total=False):
    context: RunContext
    image: ImageRef
    locale: Locale
    ocr_response: Any
    scan_quality: ScanQuality
    prepare_signals: PrepareSignals
    prepared_items: list[PreparedItem]
    warnings: Annotated[list[NodeWarning], merge_signals]
    errors: Annotated[list[NodeError], merge_signals]


@dataclass(frozen=True)
class ItemTask:
    prepared_item: PreparedItem
    knowledge: ItemKnowledge
    ingredient_graph: Any
    source_reliability: SourceReliability
    user_profile: UserProfile
    locale: Locale


class ItemState(TypedDict, total=False):
    task: ItemTask
    route: Literal["exact_only", "db_bayes", "db_variant_bayes", "web_bayes", "no_information"]
    candidate_response: Any
    exact_response: Any
    ontology_response: Any
    bayesian_response: Any
    web_response: Any
    decision_response: Any
    result: ItemResult
    evidence: Annotated[list[EvidenceRef], merge_evidence]
    commands: Annotated[list[PersistenceCommand], merge_commands]
    warnings: Annotated[list[NodeWarning], merge_signals]
    errors: Annotated[list[NodeError], merge_signals]


class JudgeState(TypedDict, total=False):
    context: RunContext
    prepared_items: list[PreparedItem]
    prepare_signals: PrepareSignals
    knowledge_bundle: KnowledgeBundle
    user_profile: UserProfile
    locale: Locale
    item_results: Annotated[list[ItemResult], merge_item_results]
    ordered_items: list[ItemResult]
    evidence: Annotated[list[EvidenceRef], merge_evidence]
    commands: Annotated[list[PersistenceCommand], merge_commands]
    warnings: Annotated[list[NodeWarning], merge_signals]
    errors: Annotated[list[NodeError], merge_signals]
