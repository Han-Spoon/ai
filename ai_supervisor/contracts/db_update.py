"""Canonical ⑦ DB Update Tool command-builder contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Union

from pydantic import StrictBool, field_validator, model_validator

from ._validation import require_item_context
from .common import (
    AmbiguityFlag,
    ConstraintTag,
    IngredientRef,
    Locale,
    NodeContext,
    NodeError,
    NodeWarning,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    Probability,
    StrictModel,
)
from .external import PreparedHash, WebSource
from .ontology import VariantSuggestion
from .web_search import ReviewNote, WebMenu

DbOperationType = Literal[
    "create_review_item",
    "create_owner_verification_request",
    "record_owner_answer",
    "update_source_reliability",
    "save_web_search_cache",
]


class CreateReviewItemPayload(StrictModel):
    review_origin: Literal["web_search", "variant_suggestion"]
    menu: WebMenu
    web_sources: list[WebSource]
    review_notes: list[ReviewNote]
    variant_suggestion: VariantSuggestion | None

    @model_validator(mode="after")
    def validate_origin(self) -> "CreateReviewItemPayload":
        if self.review_origin == "web_search":
            if not self.web_sources or self.variant_suggestion is not None:
                raise ValueError("web review requires sources and no variant suggestion")
        elif self.web_sources or self.variant_suggestion is None:
            raise ValueError("variant review requires a suggestion and no web sources")
        return self


class CreateOwnerVerificationRequestPayload(StrictModel):
    item_id: NonEmptyString
    card_id: NonEmptyString
    menu_id: PositiveInt | None
    ingredient: IngredientRef | None
    ambiguity_flag: AmbiguityFlag | None
    constraint_tags: list[ConstraintTag]
    recipe_probability: Probability | None
    question: dict[Locale, NonEmptyString]
    reason_codes: list[
        Literal[
            "insufficient_recipe_evidence",
            "anomaly",
            "no_information",
            "high_risk_review",
            "ambiguous_menu",
            "unconfirmed",
        ]
    ]
    evidence_refs: list[NonEmptyString]

    @model_validator(mode="before")
    @classmethod
    def discard_single_round_legacy_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "round" not in value and "previous_question_id" not in value:
            return value
        if value.get("round", 1) != 1 or value.get("previous_question_id") is not None:
            raise ValueError("multi-round owner requests are no longer supported")
        cleaned = dict(value)
        cleaned.pop("round", None)
        cleaned.pop("previous_question_id", None)
        return cleaned

    @property
    def round(self) -> Literal[1]:
        """Compatibility view for old internal adapters; never serialized."""

        return 1

    @property
    def previous_question_id(self) -> None:
        """Compatibility view for old internal adapters; never serialized."""

        return None

    @model_validator(mode="after")
    def validate_card(self) -> "CreateOwnerVerificationRequestPayload":
        if (self.ingredient is None) == (self.ambiguity_flag is None):
            raise ValueError("stored owner request requires exactly one target")
        if self.ingredient is not None and self.constraint_tags:
            raise ValueError("ingredient owner request must use empty constraint_tags")
        if self.ambiguity_flag is not None and not self.constraint_tags:
            raise ValueError("flag owner request requires constraint_tags")
        if "ko" not in self.question or not self.reason_codes:
            raise ValueError("owner request requires Korean text and a reason")
        if self.ambiguity_flag is not None and self.recipe_probability is not None:
            raise ValueError("flag owner request must not have recipe_probability")
        return self


class RecordOwnerAnswerPayload(StrictModel):
    menu_id: PositiveInt
    ingredient: IngredientRef | None
    ambiguity_flag: AmbiguityFlag | None
    constraint_tags: list[ConstraintTag]
    question_id: NonEmptyString
    present: StrictBool
    answered_at: datetime

    @field_validator("answered_at")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("answered_at must be UTC")
        return value

    @model_validator(mode="after")
    def validate_target(self) -> "RecordOwnerAnswerPayload":
        if (self.ingredient is None) == (self.ambiguity_flag is None):
            raise ValueError("owner answer requires exactly one target")
        if self.ingredient is not None and self.constraint_tags:
            raise ValueError("ingredient owner answer must use empty constraint_tags")
        if self.ambiguity_flag is not None and not self.constraint_tags:
            raise ValueError("flag owner answer requires constraint_tags")
        return self


class UpdateSourceReliabilityPayload(StrictModel):
    source_type: NonEmptyString
    correct_count_delta: NonNegativeInt
    comparison_count_delta: NonNegativeInt
    trigger_evidence_ref: NonEmptyString

    @model_validator(mode="after")
    def validate_counts(self) -> "UpdateSourceReliabilityPayload":
        if self.correct_count_delta > self.comparison_count_delta:
            raise ValueError("correct_count_delta must not exceed comparison_count_delta")
        return self


class SaveWebSearchCachePayload(StrictModel):
    normalized_menu_name: NonEmptyString
    original_menu_name: NonEmptyString
    search_status: Literal["searched", "unresolved"]
    sources: list[WebSource]
    next_rank: PositiveInt
    searched_at: datetime

    @field_validator("searched_at")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("searched_at must be UTC")
        return value


OperationPayload = Union[
    CreateReviewItemPayload,
    CreateOwnerVerificationRequestPayload,
    RecordOwnerAnswerPayload,
    UpdateSourceReliabilityPayload,
    SaveWebSearchCachePayload,
]

_OPERATION_PAYLOAD_TYPES = {
    "create_review_item": CreateReviewItemPayload,
    "create_owner_verification_request": CreateOwnerVerificationRequestPayload,
    "record_owner_answer": RecordOwnerAnswerPayload,
    "update_source_reliability": UpdateSourceReliabilityPayload,
    "save_web_search_cache": SaveWebSearchCachePayload,
}


class DbOperation(StrictModel):
    operation_id: NonEmptyString
    operation_type: DbOperationType
    payload: OperationPayload

    @model_validator(mode="after")
    def validate_payload_type(self) -> "DbOperation":
        expected = _OPERATION_PAYLOAD_TYPES[self.operation_type]
        if not isinstance(self.payload, expected):
            raise ValueError("operation_type does not match payload schema")
        return self


class DbUpdateRequest(StrictModel):
    context: NodeContext
    operations: list[DbOperation]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_request(self) -> "DbUpdateRequest":
        if self.context.call_scope == "item":
            require_item_context(self.context, require_store=True)
        elif self.context.call_scope != "out_of_band" or self.context.item_id is not None:
            raise ValueError("DB update requires item or out_of_band context")
        if self.context.store_id is None:
            raise ValueError("DB update requires a positive store_id")
        ids = [item.operation_id for item in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("operation_id values must be unique")
        return self


class Provenance(StrictModel):
    origin_node: Literal[
        "ontology", "web_search", "decision", "external_owner_answer", "db_update"
    ]
    source_type: Literal[
        "web_search", "ocr_variant_tag", "decision_policy", "owner_feedback", "system"
    ]
    evidence_class: Literal["hard", "soft"] | None
    evidence_refs: list[NonEmptyString]


class ReviewCommandPayload(CreateReviewItemPayload):
    provenance: Provenance


class OwnerRequestCommandPayload(CreateOwnerVerificationRequestPayload):
    provenance: Provenance


class OwnerAnswerCommandPayload(RecordOwnerAnswerPayload):
    provenance: Provenance


class ReliabilityCommandPayload(UpdateSourceReliabilityPayload):
    provenance: Provenance


class WebCacheCommandPayload(SaveWebSearchCachePayload):
    provenance: Provenance


CommandPayload = Union[
    ReviewCommandPayload,
    OwnerRequestCommandPayload,
    OwnerAnswerCommandPayload,
    ReliabilityCommandPayload,
    WebCacheCommandPayload,
]

_COMMAND_PAYLOAD_TYPES = {
    "create_review_item": ReviewCommandPayload,
    "create_owner_verification_request": OwnerRequestCommandPayload,
    "record_owner_answer": OwnerAnswerCommandPayload,
    "update_source_reliability": ReliabilityCommandPayload,
    "save_web_search_cache": WebCacheCommandPayload,
}


class DbCommand(StrictModel):
    command_id: NonEmptyString
    operation_id: NonEmptyString
    item_id: NonEmptyString | None
    card_id: NonEmptyString | None
    action: DbOperationType
    idempotency_key: PreparedHash
    payload: CommandPayload

    @model_validator(mode="after")
    def validate_command(self) -> "DbCommand":
        expected = _COMMAND_PAYLOAD_TYPES[self.action]
        if not isinstance(self.payload, expected):
            raise ValueError("command action does not match payload schema")
        is_owner_request = self.action == "create_owner_verification_request"
        if is_owner_request != (self.card_id is not None):
            raise ValueError("card_id is required only for owner request commands")
        if is_owner_request and self.card_id != self.payload.card_id:
            raise ValueError("command card_id must match payload card_id")
        return self


class DbUpdateResponse(StrictModel):
    context: NodeContext
    commands: list[DbCommand]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "DbUpdateResponse":
        if self.context.call_scope == "item":
            require_item_context(self.context, require_store=True)
        elif self.context.call_scope != "out_of_band" or self.context.item_id is not None:
            raise ValueError("DB update requires item or out_of_band context")
        if self.context.store_id is None:
            raise ValueError("DB update requires a positive store_id")
        keys = [item.idempotency_key for item in self.commands]
        if len(keys) != len(set(keys)):
            raise ValueError("idempotency_key values must be unique")
        return self
