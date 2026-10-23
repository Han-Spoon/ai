"""Canonical ⑤ Bayesian Tool request and response contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from ._validation import ensure_refs_resolve, require_item_context
from .common import (
    ConstraintTag,
    EvidenceRef,
    IngredientRef,
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
from .external import SourceReliability, StorePrior
from .ontology import (
    IngredientObservation,
    IngredientRole,
    IngredientSource,
    ObservationBasis,
)

PriorSource = Literal["store", "cluster", "global", "uninformative"]


class BayesianIngredient(StrictModel):
    ingredient: IngredientRef
    constraint_tags: list[ConstraintTag]
    role: IngredientRole | None
    source: IngredientSource
    depth: NonNegativeInt
    curated: StrictBool
    observation_basis: ObservationBasis
    observations: list[IngredientObservation]
    anomaly_locked: StrictBool
    evidence_refs: list[NonEmptyString]

    @model_validator(mode="after")
    def validate_observations(self) -> "BayesianIngredient":
        if self.observation_basis == "none" and self.observations:
            raise ValueError("observation_basis none requires empty observations")
        if self.observation_basis != "none" and not self.observations:
            raise ValueError("direct/inherited ingredients require observations")
        return self


class BayesianPriors(StrictModel):
    status: SliceStatus
    store: list[StorePrior]
    cluster: list[StorePrior]
    global_: list[StorePrior] = Field(alias="global", serialization_alias="global")
    evidence: list[EvidenceRef]

    @model_validator(mode="after")
    def validate_priors(self) -> "BayesianPriors":
        if self.cluster or self.global_:
            raise ValueError("cluster and global prior arrays must remain empty in schema 3.0")
        if self.status != "ok" and (self.store or self.evidence):
            raise ValueError("non-ok Bayesian priors must be empty")
        evidence_ids = {item.evidence_id for item in self.evidence}
        refs = [ref for prior in self.store for ref in prior.evidence_refs]
        ensure_refs_resolve(refs, evidence_ids, "Bayesian store prior evidence_refs")
        return self


class BayesianRequest(StrictModel):
    context: NodeContext
    menu_category_id: PositiveInt | None
    ingredients: list[BayesianIngredient]
    priors: BayesianPriors
    source_reliability: SourceReliability
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_context(self) -> "BayesianRequest":
        require_item_context(self.context)
        return self


class ProbabilityResult(StrictModel):
    ingredient: IngredientRef
    constraint_tags: list[ConstraintTag]
    role: IngredientRole | None
    posterior_mean: Probability
    alpha_post: Annotated[float, Field(gt=0.0)]
    beta_post: Annotated[float, Field(gt=0.0)]
    prior_source: PriorSource
    source: IngredientSource
    depth: NonNegativeInt
    confidence: Probability
    anomaly_locked: StrictBool
    evidence_refs: list[NonEmptyString]


class BayesianResponse(StrictModel):
    context: NodeContext
    probabilities: list[ProbabilityResult]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_context(self) -> "BayesianResponse":
        require_item_context(self.context)
        return self
