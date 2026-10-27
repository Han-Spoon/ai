"""LangGraph state types and deterministic reducers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypedDict
from uuid import UUID

from .contracts.common import Locale, NodeContext, NodeError, NodeWarning, PositiveInt
from .contracts.decision import FoundIngredient, ReviewReason
from .contracts.external import (
    EvidenceRef,
    ImageRef,
    CandidateKnowledge,
    ItemResult,
    KnowledgeBundle,
    OntologySlice,
    PersistenceCommand,
    PrepareSignals,
    PreparedItem,
    ScanQuality,
    SourceReliability,
    UserProfile,
    WebSearchCache,
)

if TYPE_CHECKING:
    from ai_normalization.contracts import CandidateSelectionResult


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
        self,
        call_scope: Literal["scan", "item", "out_of_band"],
        item_id: str | None,
        *,
        include_store: bool = False,
    ) -> NodeContext:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "scan_session_id": self.scan_session_id,
            "call_scope": call_scope,
            "item_id": item_id,
        }
        if include_store or self.store_id is not None:
            data["store_id"] = self.store_id
        return NodeContext.model_validate(data)


class PrepareState(TypedDict, total=False):
    image: ImageRef
    locale: Locale
    ocr_response: Any
    normalization_response: Any
    scan_quality: ScanQuality
    prepare_signals: PrepareSignals
    prepared_items: list[PreparedItem]
    response: Any
    warnings: Annotated[list[NodeWarning], merge_signals]
    errors: Annotated[list[NodeError], merge_signals]


@dataclass(frozen=True)
class ItemTaskKnowledge:
    item_id: str
    selected: CandidateKnowledge | None
    fallback_ontology: OntologySlice
    web_search_cache: WebSearchCache


@dataclass(frozen=True)
class ItemTask:
    prepared_item: PreparedItem
    selection: "CandidateSelectionResult"
    knowledge: ItemTaskKnowledge
    ingredient_graph: Any
    source_reliability: SourceReliability
    user_profile: UserProfile
    locale: Locale
    warnings: tuple[NodeWarning, ...] = ()
    errors: tuple[NodeError, ...] = ()


@dataclass(frozen=True)
class ItemAnalysisOutput:
    result: ItemResult
    evidence: tuple[EvidenceRef, ...] = ()
    commands: tuple[PersistenceCommand, ...] = ()
    review: "ItemReviewCandidate | None" = None


@dataclass(frozen=True)
class ItemReviewCandidate:
    reason_code: ReviewReason
    found_ingredients: tuple[FoundIngredient, ...]
    answered_ingredient_keys: frozenset[tuple[str, int | str]] = frozenset()


def merge_item_updates(
    current: list[ItemAnalysisOutput], update: list[ItemAnalysisOutput]
) -> list[ItemAnalysisOutput]:
    """Append concurrent item outputs once without imposing completion order."""

    merged = list(current)
    by_id = {item.result.item_id: item for item in current}
    for item in update:
        previous = by_id.get(item.result.item_id)
        if previous is not None and previous != item:
            raise ValueError(f"conflicting item update: {item.result.item_id}")
        if previous is None:
            merged.append(item)
            by_id[item.result.item_id] = item
    return merged


class ItemState(TypedDict, total=False):
    task: ItemTask
    route: Literal["exact_only", "db_bayes", "db_variant_bayes", "web_bayes", "no_information"]
    candidate_response: Any
    exact_response: Any
    ontology_response: Any
    bayesian_response: Any
    tag_lookup_response: Any
    web_response: Any
    web_collect_response: Any
    ingredient_mapping_response: Any
    web_aggregate_response: Any
    db_update_response: Any
    decision_request: Any
    decision_response: Any
    result: ItemResult
    analysis_output: ItemAnalysisOutput
    evidence: Annotated[list[EvidenceRef], merge_evidence]
    commands: Annotated[list[PersistenceCommand], merge_commands]
    warnings: Annotated[list[NodeWarning], merge_signals]
    errors: Annotated[list[NodeError], merge_signals]


class JudgeState(TypedDict, total=False):
    request: Any
    prepared_items: list[PreparedItem]
    prepare_signals: PrepareSignals
    knowledge_bundle: KnowledgeBundle
    user_profile: UserProfile
    locale: Locale
    bundle_hash: str
    candidate_selection_response: Any
    item_task: ItemTask
    item_updates: Annotated[list[ItemAnalysisOutput], merge_item_updates]
    ordered_items: list[ItemResult]
    review_candidates: dict[str, ItemReviewCandidate]
    evidence: Annotated[list[EvidenceRef], merge_evidence]
    persistence_commands: Annotated[list[PersistenceCommand], merge_commands]
    warnings: Annotated[list[NodeWarning], merge_signals]
    errors: Annotated[list[NodeError], merge_signals]
    response: Any
