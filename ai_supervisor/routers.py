"""Pure routing helpers shared by the future LangGraph builders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .contracts.exact import ExactFeedbackResponse
from .contracts.external import ScanQuality
from .contracts.ontology import OntologyResponse

if TYPE_CHECKING:
    from .state import ItemTask


def route_after_quality(quality: ScanQuality) -> Literal["normalize", "finalize"]:
    return "finalize" if quality.status == "needs_retake" else "normalize"


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
    """Skip the heavy path only when every confirmation is eligible and complete."""

    complete = response.completeness == "complete" and all(
        confirmation.override_eligible for confirmation in response.confirmations
    )
    return "tag_lookup" if complete else "ontology"


def route_after_ontology(
    response: OntologyResponse,
) -> Literal["bayesian", "variant_pending", "web_pending"]:
    """Route the implemented DB path without silently skipping later phases."""

    if response.menu.variant_origin == "runtime_tagged":
        return "variant_pending"
    if response.menu.exists_in_db or response.menu.base_menu_id is not None:
        return "bayesian"
    return "web_pending"
