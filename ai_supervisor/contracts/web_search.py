"""Canonical ⑥ Web Search Agent two-stage contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import StrictBool, model_validator

from ._validation import require_item_context
from .bayesian import BayesianIngredient
from .common import (
    ConstraintTag,
    IngredientRef,
    Locale,
    NodeContext,
    NodeError,
    NodeWarning,
    NonEmptyString,
    PositiveInt,
    StrictModel,
)
from .external import WebSource
from .ontology import IngredientRole


class WebCollectMenu(StrictModel):
    menu_id: PositiveInt | None
    normalized_menu_name: NonEmptyString
    locale_hint: Locale | None


class WebMenu(StrictModel):
    menu_id: PositiveInt | None
    normalized_menu_name: NonEmptyString


class WebCollectRequest(StrictModel):
    context: NodeContext
    menu: WebCollectMenu
    max_sources: Literal[10]
    cached_sources: list[WebSource]
    next_rank: PositiveInt
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_context(self) -> "WebCollectRequest":
        require_item_context(self.context)
        return self


class WebCollectResponse(StrictModel):
    context: NodeContext
    menu: WebMenu
    found: StrictBool
    sources: list[WebSource]
    next_rank: PositiveInt
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "WebCollectResponse":
        require_item_context(self.context)
        if self.found != bool(self.sources):
            raise ValueError("WebCollect found must match source presence")
        return self


class MappedIngredient(StrictModel):
    ingredient: IngredientRef
    constraint_tags: list[ConstraintTag]
    role: IngredientRole | None


class MappedSource(StrictModel):
    source_url: NonEmptyString
    title: NonEmptyString
    source_origin: Literal["new", "cache"]
    corpus: NonEmptyString
    evidence_id: NonEmptyString
    ingredients: list[MappedIngredient]


class WebAggregateRequest(StrictModel):
    context: NodeContext
    menu: WebMenu
    mapped_sources: list[MappedSource]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_context(self) -> "WebAggregateRequest":
        require_item_context(self.context)
        return self


class ReviewNote(StrictModel):
    rule_code: Literal[
        "general_web_source",
        "title_mismatch",
        "inconsistent_recipes",
        "rare_ingredient",
        "llm_only_ingredient",
        "unmapped_ingredient",
        "risky_ingredient_partial",
    ]
    message: NonEmptyString


class WebAggregateResponse(StrictModel):
    context: NodeContext
    menu: WebMenu
    found: StrictBool
    sources: list[WebSource]
    ingredients: list[BayesianIngredient]
    review_notes: list[ReviewNote]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "WebAggregateResponse":
        require_item_context(self.context)
        if self.found != bool(self.ingredients):
            raise ValueError("WebAggregate found must match aggregated ingredient presence")
        if any(item.source != "web_search" for item in self.ingredients):
            raise ValueError("WebAggregate ingredients must use web_search source")
        if any(item.curated for item in self.ingredients):
            raise ValueError("WebAggregate ingredients must not be curated")
        if any(item.observation_basis != "direct" for item in self.ingredients):
            raise ValueError("WebAggregate ingredients must use direct observations")
        return self
