"""Canonical ⑧ Decision Policy / XAI Agent contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import StrictBool, model_validator

from ._validation import ensure_unique, require_item_context
from .bayesian import PriorSource
from .common import (
    AmbiguityFlag,
    ConstraintTag,
    FlagConfirmation,
    IngredientRef,
    IngredientStatus,
    Locale,
    LocalizedIngredientRef,
    NodeContext,
    NodeError,
    NodeWarning,
    NonEmptyString,
    PositiveInt,
    Probability,
    StrictModel,
    UserProfile,
)
from .external import DecisionMessage, OwnerCard
from .ontology import IngredientRole

RiskLevel = Literal["danger", "caution", "safe"]
EvidenceBasis = Literal["confirmed", "estimated", "unknown"]
InformationStatus = Literal["complete", "partial", "none"]
ReviewReason = Literal[
    "anomaly",
    "unknown_tag",
    "no_information",
    "partial_information",
    "insufficient_web_sources",
]


class DecisionMenu(StrictModel):
    menu_id: PositiveInt | None
    menu_category_id: PositiveInt | None
    normalized_menu_name: NonEmptyString


class ConfirmedResult(StrictModel):
    ingredient: IngredientRef
    constraint_tags: list[ConstraintTag]
    role: IngredientRole | None
    status: IngredientStatus
    override_eligible: StrictBool
    evidence_refs: list[NonEmptyString]


class DecisionProbabilityResult(StrictModel):
    ingredient: IngredientRef
    constraint_tags: list[ConstraintTag]
    role: IngredientRole | None
    posterior_mean: Probability
    confidence: Probability | None
    prior_source: PriorSource | None
    anomaly_locked: StrictBool
    evidence_refs: list[NonEmptyString]


class ProposedVariantIngredient(StrictModel):
    ingredient: IngredientRef
    constraint_tags: list[ConstraintTag]
    role: IngredientRole | None
    source: Literal["variant_suggested"]
    evidence_refs: list[NonEmptyString]


class DecisionTagObservation(StrictModel):
    tag: ConstraintTag
    confidence: Probability
    evidence_refs: list[NonEmptyString]


class LocalizedName(StrictModel):
    ingredient_id: PositiveInt
    localized_name: NonEmptyString


class DecisionRequest(StrictModel):
    context: NodeContext
    menu: DecisionMenu
    confirmed_results: list[ConfirmedResult]
    probability_results: list[DecisionProbabilityResult]
    proposed_variant_ingredients: list[ProposedVariantIngredient]
    risk_tag_observations: list[DecisionTagObservation]
    menu_ambiguity_flags: list[AmbiguityFlag]
    flag_confirmations: list[FlagConfirmation]
    no_information: StrictBool
    information_status: InformationStatus
    owner_interaction_allowed: StrictBool
    user_profile: UserProfile
    locale: Locale
    localized_names: list[LocalizedName]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_request(self) -> "DecisionRequest":
        require_item_context(self.context)
        if self.owner_interaction_allowed != (self.context.store_id is not None):
            raise ValueError("owner_interaction_allowed must match store_id presence")
        ids = [item.ingredient_id for item in self.localized_names]
        ensure_unique(ids, "localized ingredient IDs")
        if self.context.store_id is None and not any(
            warning.code == "store_context_missing" for warning in self.warnings
        ):
            raise ValueError("storeless decision requires store_context_missing warning")
        return self


class DecisionRiskIngredient(StrictModel):
    ingredient: LocalizedIngredientRef
    constraint_tags: list[ConstraintTag]
    matched_tags: list[ConstraintTag]
    role: IngredientRole | None
    status: IngredientStatus
    posterior_mean: Probability | None
    confidence: Probability | None
    prior_source: PriorSource | None
    anomaly_locked: StrictBool
    evidence_refs: list[NonEmptyString]


class HighRiskReview(StrictModel):
    required: StrictBool
    reason_code: ReviewReason | None

    @model_validator(mode="after")
    def validate_reason(self) -> "HighRiskReview":
        if self.required != (self.reason_code is not None):
            raise ValueError("high-risk review reason must match required")
        return self


class DecisionResponse(StrictModel):
    context: NodeContext
    menu: DecisionMenu
    risk_level: RiskLevel
    evidence_basis: EvidenceBasis
    information_status: InformationStatus
    risk_ingredients: list[DecisionRiskIngredient]
    matched_tags: list[ConstraintTag]
    evidence_refs: list[NonEmptyString]
    message: DecisionMessage
    owner_cards: list[OwnerCard]
    high_risk_review: HighRiskReview
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "DecisionResponse":
        require_item_context(self.context)
        if any(card.item_id != self.context.item_id for card in self.owner_cards):
            raise ValueError("decision owner cards must belong to the current item")
        if self.context.store_id is None:
            if self.risk_level == "safe" or self.owner_cards or self.high_risk_review.required:
                raise ValueError("storeless decision cannot be safe or create owner interaction")
        return self


class ReviewMenu(StrictModel):
    menu_id: PositiveInt | None
    normalized_menu_name: NonEmptyString
    raw_menu_name: NonEmptyString
    description: str | None


class ReviewDecision(StrictModel):
    risk_level: RiskLevel
    evidence_basis: EvidenceBasis
    message: DecisionMessage
    owner_cards: list[OwnerCard]


class FoundIngredient(StrictModel):
    ingredient: IngredientRef
    constraint_tags: list[ConstraintTag]


class IngredientChoice(LocalizedIngredientRef):
    constraint_tags: list[ConstraintTag]


class HighRiskReviewRequest(StrictModel):
    context: NodeContext
    menu: ReviewMenu
    reason_code: ReviewReason
    decision: ReviewDecision
    found_ingredients: list[FoundIngredient]
    ingredient_choices: list[IngredientChoice]
    user_profile: UserProfile
    locale: Locale
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_request(self) -> "HighRiskReviewRequest":
        require_item_context(self.context, require_store=True)
        ids = [item.ingredient_id for item in self.ingredient_choices]
        ensure_unique(ids, "high-risk ingredient choice IDs")
        return self


class HighRiskReviewResponse(StrictModel):
    context: NodeContext
    missed_ingredients: list[IngredientRef]
    reason: str | None
    owner_cards: list[OwnerCard]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "HighRiskReviewResponse":
        require_item_context(self.context, require_store=True)
        if any(card.item_id != self.context.item_id for card in self.owner_cards):
            raise ValueError("review owner cards must belong to the current item")
        return self
