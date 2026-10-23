"""Canonical ④ DB / Ontology Tool request and response contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from ._validation import ensure_refs_resolve, ensure_unique, require_item_context
from .common import (
    AmbiguityFlag,
    ConstraintTag,
    EvidenceRef,
    IngredientRef,
    NodeContext,
    NodeError,
    NodeWarning,
    NonEmptyString,
    NonNegativeInt,
    PositiveInt,
    StrictModel,
)
from .external import (
    Candidate,
    IngredientGraph,
    OntologySlice,
    PersistenceCommand,
)

IngredientRole = Literal["identity", "component", "hidden"]
IngredientSource = Literal["recipe", "expanded", "variant_suggested", "web_search"]
ObservationBasis = Literal["direct", "inherited", "none"]


class OntologyMenuInput(StrictModel):
    menu_id: PositiveInt | None
    normalized_menu_name: NonEmptyString
    match_candidates: Annotated[list[Candidate], Field(max_length=5)]
    residual_tokens: list[NonEmptyString]


class ConfirmedIngredient(StrictModel):
    ingredient: IngredientRef
    status: Literal["present", "absent"]


class OntologyRequest(StrictModel):
    context: NodeContext
    menu: OntologyMenuInput
    unconfirmed_only: StrictBool
    confirmed_ingredients: list[ConfirmedIngredient]
    anomaly_locked_ingredients: list[IngredientRef]
    knowledge: OntologySlice
    ingredient_graph: IngredientGraph
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_request(self) -> "OntologyRequest":
        require_item_context(self.context)
        candidate_ids = [candidate.menu_id for candidate in self.menu.match_candidates]
        ensure_unique(candidate_ids, "ontology match candidate IDs")
        if self.menu.menu_id is not None and self.menu.menu_id not in candidate_ids:
            raise ValueError("selected menu_id must be present in match_candidates")
        return self


class OntologyResultMenu(StrictModel):
    menu_id: PositiveInt | None
    base_menu_id: PositiveInt | None
    remain_token: NonEmptyString | None
    menu_category_id: PositiveInt | None
    menu_category: str | None
    exists_in_db: StrictBool
    is_variant: StrictBool
    variant_origin: Literal["db_registered", "runtime_tagged"] | None

    @model_validator(mode="after")
    def validate_menu_shape(self) -> "OntologyResultMenu":
        if self.exists_in_db != (self.menu_id is not None):
            raise ValueError("exists_in_db must match menu_id presence")
        if (self.base_menu_id is None) != (self.remain_token is None):
            raise ValueError("base_menu_id and remain_token must be set together")
        if self.is_variant != (self.base_menu_id is not None):
            raise ValueError("is_variant must match base_menu_id presence")
        if self.is_variant != (self.variant_origin is not None):
            raise ValueError("variant menus require variant_origin only")
        if self.variant_origin == "db_registered" and not self.exists_in_db:
            raise ValueError("db_registered variant must exist in DB")
        if self.variant_origin == "runtime_tagged" and self.exists_in_db:
            raise ValueError("runtime_tagged variant must not exist in DB")
        return self


class IngredientObservation(StrictModel):
    corpus: NonEmptyString
    k_count: NonNegativeInt
    n_total: NonNegativeInt

    @model_validator(mode="after")
    def validate_counts(self) -> "IngredientObservation":
        if self.k_count > self.n_total:
            raise ValueError("k_count must not exceed n_total")
        return self


class OntologyIngredient(StrictModel):
    ingredient: IngredientRef
    taxonomy_category: str | None = None
    constraint_tags: list[ConstraintTag]
    role: IngredientRole | None
    depth: NonNegativeInt
    parent_ingredient: IngredientRef | None = None
    source: IngredientSource
    curated: StrictBool
    observation_basis: ObservationBasis
    observations: list[IngredientObservation]
    anomaly_locked: StrictBool
    evidence_refs: list[NonEmptyString]

    @model_validator(mode="after")
    def validate_observations(self) -> "OntologyIngredient":
        if self.observation_basis == "none" and self.observations:
            raise ValueError("observation_basis none requires empty observations")
        if self.observation_basis != "none" and not self.observations:
            raise ValueError("direct/inherited ingredients require observations")
        return self


class SuggestedIngredient(StrictModel):
    ingredient: IngredientRef
    constraint_tags: list[ConstraintTag]
    role: IngredientRole | None


class VariantSuggestion(StrictModel):
    base_menu_id: PositiveInt
    remain_token: NonEmptyString
    suggested_ingredients: list[SuggestedIngredient]
    evidence: EvidenceRef


class OntologyResponse(StrictModel):
    context: NodeContext
    menu: OntologyResultMenu
    menu_ambiguity_flags: list[AmbiguityFlag]
    ingredients: list[OntologyIngredient]
    evidence: list[EvidenceRef]
    variant_suggestion: VariantSuggestion | None
    unmapped_tokens: list[NonEmptyString]
    cache_command: PersistenceCommand | None
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "OntologyResponse":
        require_item_context(self.context)
        evidence_ids = [item.evidence_id for item in self.evidence]
        ensure_unique(evidence_ids, "ontology evidence IDs")
        refs = [ref for item in self.ingredients for ref in item.evidence_refs]
        ensure_refs_resolve(refs, set(evidence_ids), "ontology ingredient evidence_refs")
        if (self.menu.variant_origin == "runtime_tagged") != (
            self.variant_suggestion is not None
        ):
            raise ValueError("runtime_tagged menus require exactly one variant_suggestion")
        if self.cache_command is not None:
            if self.cache_command.action != "save_menu_ingredient_cache":
                raise ValueError("ontology cache command must save_menu_ingredient_cache")
            if self.context.store_id is None:
                raise ValueError("storeless ontology response cannot contain cache command")
        return self


class TagLookupRequest(StrictModel):
    context: NodeContext
    ingredient_ids: list[PositiveInt]
    ingredient_graph: IngredientGraph
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_request(self) -> "TagLookupRequest":
        require_item_context(self.context)
        ensure_unique(self.ingredient_ids, "tag lookup ingredient IDs")
        return self


class TagLookupResult(StrictModel):
    ingredient_id: PositiveInt
    constraint_tags: list[ConstraintTag]
    role: IngredientRole | None


class TagLookupResponse(StrictModel):
    context: NodeContext
    results: list[TagLookupResult]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "TagLookupResponse":
        require_item_context(self.context)
        ensure_unique(
            [result.ingredient_id for result in self.results],
            "tag lookup result ingredient IDs",
        )
        return self
