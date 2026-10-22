"""Closed schemas for the two phases of node ② Menu Normalization."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import Field, field_validator, model_validator

from ai_supervisor.contracts.common import (
    Locale,
    NodeContext,
    NodeError,
    NodeWarning,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    Probability,
    SliceStatus,
    StrictModel,
)
from ai_supervisor.contracts.external import Candidate, PriceOption, RiskTagObservation

CleaningStatus = Literal["cleaned", "unchanged", "invalid"]
NormalizationStatus = Literal["normalized", "ambiguous", "unmatched", "invalid"]
SelectionMethod = Literal["deterministic", "llm", "none"]
SelectionReasonCode = Literal[
    "exact_unique",
    "score_and_margin",
    "llm_context_resolved",
    "multiple_close_candidates",
    "below_accept_threshold",
    "candidate_not_found",
    "candidate_unavailable",
    "invalid_input",
    "llm_failed",
]


class OcrMenuItem(StrictModel):
    item_id: NonEmptyString
    source_index: NonNegativeInt
    raw_menu_name: NonEmptyString
    description: str | None
    price_text: str | None
    price_options: list[PriceOption]
    ocr_confidence: Probability
    locale_hint: Locale | None
    risk_tag_observations: list[RiskTagObservation]


class CleanedMenuItem(OcrMenuItem):
    normalized_menu_name: str | None
    display_name: str
    options: list[NonEmptyString]
    residual_tokens: list[NonEmptyString]
    cleaning_status: CleaningStatus

    @model_validator(mode="after")
    def validate_cleaning_result(self) -> "CleanedMenuItem":
        if self.cleaning_status == "invalid":
            if self.normalized_menu_name is not None:
                raise ValueError("invalid items require normalized_menu_name null")
        elif not self.normalized_menu_name:
            raise ValueError("valid items require a non-empty normalized_menu_name")
        return self


class NameCleaningRequest(StrictModel):
    context: NodeContext
    items: list[OcrMenuItem]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_request(self) -> "NameCleaningRequest":
        _validate_scan_batch(self.context, self.items, allow_store=False)
        return self


class NameCleaningResponse(StrictModel):
    context: NodeContext
    items: list[CleanedMenuItem]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "NameCleaningResponse":
        _validate_scan_batch(self.context, self.items, allow_store=False)
        _validate_source_order(self.items)
        return self


class CandidateSelectionItem(StrictModel):
    item_id: NonEmptyString
    source_index: NonNegativeInt
    raw_menu_name: NonEmptyString
    normalized_menu_name: str | None
    display_name: str
    description: str | None
    price_text: str | None
    price_options: list[PriceOption]
    ocr_confidence: Probability
    locale_hint: Locale | None
    options: list[NonEmptyString]
    residual_tokens: list[NonEmptyString]
    risk_tag_observations: list[RiskTagObservation]
    candidate_status: SliceStatus
    match_candidates: Annotated[list[Candidate], Field(max_length=5)]

    @model_validator(mode="after")
    def validate_candidate_shape(self) -> "CandidateSelectionItem":
        if self.candidate_status == "ok" and not self.match_candidates:
            raise ValueError("candidate_status ok requires at least one candidate")
        if self.candidate_status != "ok" and self.match_candidates:
            raise ValueError("non-ok candidate status requires an empty candidate list")
        ids = [candidate.menu_id for candidate in self.match_candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate menu_id values must be unique")
        expected = sorted(
            self.match_candidates,
            key=lambda candidate: (-candidate.score, candidate.menu_id),
        )
        if self.match_candidates != expected:
            raise ValueError("candidates must be sorted by score desc, menu_id asc")
        return self


class CandidateSelectionResult(CandidateSelectionItem):
    normalization_status: NormalizationStatus
    selection_method: SelectionMethod
    selection_reason_codes: list[SelectionReasonCode]
    selected_candidate: Candidate | None

    @field_validator("selection_reason_codes")
    @classmethod
    def reasons_must_be_unique(
        cls, value: list[SelectionReasonCode]
    ) -> list[SelectionReasonCode]:
        if not value:
            raise ValueError("at least one selection reason is required")
        if len(value) != len(set(value)):
            raise ValueError("selection reason codes must be unique")
        return value

    @model_validator(mode="after")
    def validate_selection(self) -> "CandidateSelectionResult":
        if self.normalization_status == "normalized":
            if self.selected_candidate is None:
                raise ValueError("normalized items require a selected candidate")
            if self.selection_method not in {"deterministic", "llm"}:
                raise ValueError("normalized items require deterministic or llm selection")
            if self.selected_candidate not in self.match_candidates:
                raise ValueError("selected_candidate must be copied from match_candidates")
        elif self.selected_candidate is not None or self.selection_method != "none":
            raise ValueError("non-normalized items require no selected candidate or method")
        return self


class CandidateSelectionRequest(StrictModel):
    context: NodeContext
    items: list[CandidateSelectionItem]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_request(self) -> "CandidateSelectionRequest":
        _validate_scan_batch(self.context, self.items, allow_store=True)
        return self


class CandidateSelectionResponse(StrictModel):
    context: NodeContext
    items: list[CandidateSelectionResult]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "CandidateSelectionResponse":
        _validate_scan_batch(self.context, self.items, allow_store=True)
        _validate_source_order(self.items)
        return self


class NameResolution(StrictModel):
    normalized_menu_name: NonEmptyString
    confidence: Probability


class NameResolutionInput(StrictModel):
    display_name: NonEmptyString
    normalized_menu_name: NonEmptyString
    locale_hint: Locale | None
    residual_tokens: list[NonEmptyString]


class CandidateResolutionCandidate(StrictModel):
    menu_id: PositiveInt
    canonical_name: NonEmptyString


class CandidateResolutionInput(StrictModel):
    raw_menu_name: NonEmptyString
    normalized_menu_name: NonEmptyString
    display_name: NonEmptyString
    description: str | None
    locale_hint: Locale | None
    options: list[NonEmptyString]
    residual_tokens: list[NonEmptyString]
    candidates: Annotated[
        list[CandidateResolutionCandidate], Field(min_length=1, max_length=5)
    ]


class CandidateResolution(StrictModel):
    selected_menu_id: PositiveInt
    confidence: Probability
    reason_code: Literal[
        "description_supports_candidate",
        "locale_supports_candidate",
        "option_supports_candidate",
    ]


class NameResolver(Protocol):
    async def resolve_name(self, item: NameResolutionInput) -> NameResolution:
        """Resolve only a multilingual name; never return a menu ID."""
        ...


class CandidateResolver(Protocol):
    async def resolve_candidate(
        self, item: CandidateResolutionInput
    ) -> CandidateResolution:
        """Choose only from item.match_candidates using item context."""
        ...


def _validate_scan_batch(
    context: NodeContext, items: list[object], *, allow_store: bool
) -> None:
    if context.call_scope != "scan" or context.item_id is not None:
        raise ValueError("normalization batch context must use scan scope")
    has_store_field = "store_id" in context.model_fields_set
    if not allow_store and has_store_field:
        raise ValueError("name cleaning must not receive store_id, including null")
    if allow_store and not has_store_field:
        raise ValueError("candidate selection requires store_id or explicit null")
    item_ids = [getattr(item, "item_id") for item in items]
    indexes = [getattr(item, "source_index") for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("item_id values must be unique")
    if len(indexes) != len(set(indexes)):
        raise ValueError("source_index values must be unique")


def _validate_source_order(items: list[object]) -> None:
    indexes = [getattr(item, "source_index") for item in items]
    if indexes != sorted(indexes):
        raise ValueError("items must be ordered by source_index")
