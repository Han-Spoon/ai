"""③ Exact Feedback Tool: read owner confirmations for one menu (docs/3-tool-exact.md).

Pure lookup. No DB access, no LLM, no calls to other nodes. ``ExactFeedbackTool`` wraps
the core in the ⓪ §4-2 request/response contract.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from ai_supervisor.contracts.common import (
    Confirmation,
    FlagConfirmation,
    OwnerQuestionState,
)
from ai_supervisor.contracts.exact import ExactFeedbackRequest, ExactFeedbackResponse
from ai_supervisor.contracts.external import (
    CandidateKnowledge,
    ExactFeedbackSlice,
    ItemKnowledge,
    MenuIngredientCache,
)

# Korea has no daylight saving time, so a fixed offset is exact (§1-7).
KST = timezone(timedelta(hours=9))
EXPIRY_MONTHS = 3

Completeness = Literal["complete", "unknown"]


class StoreIdRequiredError(ValueError):
    """③ must never fall back to a global lookup without a store (§1-1, §4-2)."""


@dataclass(frozen=True)
class ExactFeedbackOutcome:
    completeness: Completeness
    confirmations: list[Confirmation]
    flag_confirmations: list[FlagConfirmation]
    owner_question_states: list[OwnerQuestionState]


def require_store_id(store_id: int | None) -> int:
    if store_id is None or isinstance(store_id, bool) or store_id <= 0:
        raise StoreIdRequiredError("③ requires a positive store_id")
    return store_id


def add_months(day: date, months: int) -> date:
    """Calendar month addition; a missing day moves back to the month's last day."""

    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def is_expired(confirmed_at: datetime, now: datetime) -> bool:
    """True once the KST calendar date is past ``confirmed_at`` + 3 months (§1-7)."""

    if confirmed_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("confirmed_at and now must be timezone-aware")
    confirmed_day = confirmed_at.astimezone(KST).date()
    today = now.astimezone(KST).date()
    return today > add_months(confirmed_day, EXPIRY_MONTHS)


def resolve_override_eligibility(confirmation: Confirmation) -> bool:
    """Anomalous or expired confirmations never override; they stay in the list (§1-6)."""

    return not confirmation.flagged_anomaly and not confirmation.expired


def refresh_confirmation(confirmation: Confirmation, now: datetime) -> Confirmation:
    """Recompute ③-owned fields and ignore whatever the backend sent for them."""

    expired = is_expired(confirmation.evidence.observed_at, now)
    refreshed = confirmation.model_copy(update={"constraint_tags": [], "expired": expired})
    return refreshed.model_copy(
        update={"override_eligible": resolve_override_eligibility(refreshed)}
    )


def refresh_flag_confirmation(flag: FlagConfirmation, now: datetime) -> FlagConfirmation:
    """Flag answers have no anomaly check; only expiry blocks override (§1-8)."""

    expired = is_expired(flag.evidence.observed_at, now)
    return flag.model_copy(update={"expired": expired, "override_eligible": not expired})


def _snapshot_ingredient_ids(cache: MenuIngredientCache) -> set[int] | None:
    """Ingredient IDs in the cached expansion, or None when any entry is unreadable."""

    ids: set[int] = set()
    for entry in cache.ingredients_snapshot:
        ingredient: Any = entry.get("ingredient")
        ingredient_id = ingredient.get("ingredient_id") if isinstance(ingredient, dict) else None
        if not isinstance(ingredient_id, int) or isinstance(ingredient_id, bool):
            return None
        ids.add(ingredient_id)
    return ids


def check_completeness(
    confirmations: list[Confirmation], cache: MenuIngredientCache | None
) -> Completeness:
    """``complete`` only when the cache exists, every cached ingredient is confirmed,
    the two lists match, and no confirmation is anomalous or expired (§2).
    Anything uncertain falls back to ``unknown`` so ④⑤ still run."""

    if cache is None:
        return "unknown"
    snapshot_ids = _snapshot_ingredient_ids(cache)
    if not snapshot_ids:
        return "unknown"
    if any(
        c.flagged_anomaly or c.expired or c.status == "unknown" for c in confirmations
    ):
        return "unknown"
    confirmed_ids = {c.ingredient.ingredient_id for c in confirmations}
    return "complete" if confirmed_ids == snapshot_ids else "unknown"


def find_candidate_knowledge(item: ItemKnowledge, menu_id: int) -> CandidateKnowledge | None:
    """Read only the slice of this item's chosen menu; never another menu or store (§0)."""

    for knowledge in item.candidate_knowledge:
        if knowledge.menu_id == menu_id:
            return knowledge
    return None


def evaluate_exact_feedback(
    store_id: int | None,
    exact_feedback: ExactFeedbackSlice,
    cache: MenuIngredientCache | None,
    now: datetime | None = None,
) -> ExactFeedbackOutcome:
    """Core of ③ §2-2. ``not_found`` and ``unavailable`` both read as "no records";
    the SAFE-blocking warning for ``unavailable`` is added by the Supervisor."""

    require_store_id(store_id)
    now = now or datetime.now(timezone.utc)
    exact = exact_feedback.exact
    if exact is None:
        return ExactFeedbackOutcome("unknown", [], [], [])

    confirmations = [refresh_confirmation(c, now) for c in exact.confirmations]
    flag_confirmations = [refresh_flag_confirmation(f, now) for f in exact.flag_confirmations]
    return ExactFeedbackOutcome(
        completeness=check_completeness(confirmations, cache),
        confirmations=confirmations,
        flag_confirmations=flag_confirmations,
        owner_question_states=list(exact.owner_question_states),
    )


class ExactFeedbackTool:
    """③ node for one menu item, matching the Supervisor ``NodeAdapter`` shape.

    The request carries no bundle data (⓪ §4-2), so the Item Graph hands over the same
    item's ``ItemKnowledge`` when it creates the tool (⓪ §2-6). Reading any other item's
    or menu's data is a contract error, never a silent fallback.
    """

    def __init__(
        self,
        knowledge: ItemKnowledge,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._knowledge = knowledge
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def run(self, request: ExactFeedbackRequest) -> ExactFeedbackResponse:
        if request.context.item_id != self._knowledge.item_id:
            raise ValueError("③ may only read the knowledge of its own item_id")
        menu_id = request.menu.menu_id
        candidate = find_candidate_knowledge(self._knowledge, menu_id)
        if candidate is None:
            raise ValueError(f"no candidate knowledge for menu_id {menu_id}")
        exact = candidate.exact_feedback.exact
        if exact is not None and exact.menu_id != menu_id:
            raise ValueError("exact_feedback belongs to a different menu_id")

        outcome = evaluate_exact_feedback(
            request.context.store_id,
            candidate.exact_feedback,
            candidate.ontology.cache,
            now=self._clock(),
        )
        return ExactFeedbackResponse(
            context=request.context,
            menu_id=menu_id,
            confirmation_scope=request.scope_hint,
            completeness=outcome.completeness,
            confirmations=outcome.confirmations,
            flag_confirmations=outcome.flag_confirmations,
            owner_question_states=outcome.owner_question_states,
            warnings=list(request.warnings),
            errors=list(request.errors),
        )
