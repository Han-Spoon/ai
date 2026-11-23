"""Pure routing helpers shared by the future LangGraph builders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .contracts.exact import ExactFeedbackResponse
from .contracts.external import ScanQuality
from .contracts.ontology import OntologyResponse
from .contracts.web_search import WebAggregateResponse, WebCollectResponse

if TYPE_CHECKING:
    from .state import ItemTask


def route_after_quality(quality: ScanQuality) -> Literal["normalize", "finalize"]:
    """Only OCR results explicitly marked usable may enter Normalization."""

    return "normalize" if quality.status == "usable" else "finalize"


def owner_interaction_allowed(store_id: int | None) -> bool:
    return store_id is not None


def route_item_start(
    task: "ItemTask", store_id: int | None
) -> Literal["exact", "ontology"]:
    """③ only runs for a selected menu inside a concrete store."""

    return "exact" if task.knowledge.selected is not None and store_id is not None else "ontology"


def route_after_exact(
    response: ExactFeedbackResponse,
) -> Literal["tag_lookup", "ontology"]:
    """Skip the heavy path when every owner target has a known answer."""

    return "tag_lookup" if response.completeness == "complete" else "ontology"


def route_after_ontology(
    response: OntologyResponse,
) -> Literal["bayesian", "variant_review", "web_search"]:
    """Recover an actually empty DB recipe through web search.

    ``ingredients`` can also be empty because owner confirmations removed every
    remaining target, so only ④'s pre-confirmation ``empty_recipe`` warning is a safe
    signal that the registered menu has no recipe knowledge at all.
    """

    empty_recipe = any(warning.code == "empty_recipe" for warning in response.warnings)
    # An empty base recipe must be recovered before runtime-variant handling. Otherwise
    # a name such as "치즈 ○○" would estimate only the residual cheese token and
    # silently miss every ingredient of the base dish.
    if empty_recipe:
        return "web_search"
    if response.menu.variant_origin == "runtime_tagged":
        return "variant_review"
    if (
        response.menu.exists_in_db or response.menu.base_menu_id is not None
    ):
        return "bayesian"
    return "web_search"


def route_after_web_collect(
    response: WebCollectResponse, *, has_cached_sources: bool
) -> Literal["ingredient_mapping", "decision_no_information"]:
    """Use cached recipes even when this scan did not collect a new source."""

    return (
        "ingredient_mapping"
        if response.sources or has_cached_sources
        else "decision_no_information"
    )


def route_after_web_aggregate(
    response: WebAggregateResponse,
) -> Literal["web_review", "decision_no_information"]:
    return "web_review" if response.found else "decision_no_information"
