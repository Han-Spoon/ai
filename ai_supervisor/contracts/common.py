"""Shared closed-schema models used at Supervisor boundaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "3.0"

SchemaVersion = Literal["3.0"]
Locale = Literal["ko", "en", "ar", "zh-CN", "zh-TW", "ja", "es"]
CallScope = Literal["scan", "item", "out_of_band"]
IngredientStatus = Literal["present", "absent", "unknown"]
SliceStatus = Literal["ok", "not_found", "unavailable"]
AmbiguityFlag = Literal[
    "has_unclear_broth",
    "has_unclear_seasoning",
    "has_unclear_jeotgal",
    "has_hidden_animal",
    "has_variant",
]
ConstraintTag = Annotated[str, Field(pattern=r"^is_[a-z0-9_]+$")]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
NonEmptyString = Annotated[str, Field(min_length=1)]


class StrictModel(BaseModel):
    """Base for every public wire object; unknown fields are contract errors."""

    model_config = ConfigDict(extra="forbid")


def _ensure_unique(values: list[str], field_name: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class NodeContext(StrictModel):
    schema_version: SchemaVersion
    trace_id: UUID
    scan_session_id: NonEmptyString
    store_id: PositiveInt | None = None
    call_scope: CallScope
    item_id: NonEmptyString | None

    @model_validator(mode="after")
    def validate_scope(self) -> "NodeContext":
        if self.call_scope == "item" and self.item_id is None:
            raise ValueError("item_id is required when call_scope is item")
        if self.call_scope != "item" and self.item_id is not None:
            raise ValueError("item_id must be null unless call_scope is item")
        return self


class IngredientRef(StrictModel):
    ingredient_id: PositiveInt | None
    canonical_name: NonEmptyString


class LocalizedIngredientRef(IngredientRef):
    localized_name: NonEmptyString


class UserProfile(StrictModel):
    religion_type: Literal["halal", "kosher", "hindu"] | None
    is_vegetarian: StrictBool
    vegetarian_type: Literal["vegan", "lacto", "ovo", "lacto_ovo", "pesco"] | None
    no_alcohol: StrictBool
    allergies: list[ConstraintTag]
    no_spicy: StrictBool

    @field_validator("allergies")
    @classmethod
    def unique_allergies(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "allergies")

    @model_validator(mode="after")
    def validate_vegetarian_type(self) -> "UserProfile":
        if self.is_vegetarian != (self.vegetarian_type is not None):
            raise ValueError(
                "vegetarian_type is required exactly when is_vegetarian is true"
            )
        return self


class EvidenceRef(StrictModel):
    evidence_id: NonEmptyString
    evidence_class: Literal["hard", "soft"]
    source_type: Literal[
        "owner_feedback",
        "recipe",
        "expanded",
        "web_search",
        "ocr_variant_tag",
        "ocr_text_hint",
    ]
    source_ref: NonEmptyString
    verification_status: Literal[
        "owner_confirmed", "approved", "pending_review", "rejected", "inferred"
    ]
    reliability_weight: Probability | None
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def utc_only(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("observed_at must be an UTC ISO-8601 timestamp")
        return value


class NodeWarning(StrictModel):
    code: NonEmptyString
    node: NonEmptyString
    item_id: NonEmptyString | None
    message: NonEmptyString
    forces_caution: StrictBool

    @model_validator(mode="after")
    def validate_safety_warning(self) -> "NodeWarning":
        safety_codes = {
            "broken_base_menu",
            "candidate_llm_failed",
            "candidate_unavailable",
            "constraint_tags_lookup_failed",
            "cycle_detected",
            "empty_ingredients",
            "empty_recipe",
            "insufficient_sources",
            "integrity_error",
            "max_depth_reached",
            "invalid_llm_candidate",
            "unmapped_constraint_tags",
            "knowledge_slice_unavailable",
            "low_ocr_confidence",
            "scan_quality_low_confidence",
            "multiple_close_candidates",
            "store_context_missing",
            "web_llm_extraction_failed",
            "web_search_deadline_exceeded",
        }
        if self.code in safety_codes and not self.forces_caution:
            raise ValueError(f"{self.code} must force caution")
        return self


class NodeError(StrictModel):
    code: NonEmptyString
    node: NonEmptyString
    item_id: NonEmptyString | None
    message: NonEmptyString
    retryable: StrictBool
    fallback: NonEmptyString | None


class Confirmation(StrictModel):
    ingredient: IngredientRef
    constraint_tags: list[ConstraintTag]
    status: IngredientStatus
    evidence: EvidenceRef
    flagged_anomaly: StrictBool
    override_eligible: StrictBool


class FlagConfirmation(StrictModel):
    ambiguity_flag: AmbiguityFlag
    constraint_tags: list[ConstraintTag]
    status: IngredientStatus
    evidence: EvidenceRef
    override_eligible: StrictBool
