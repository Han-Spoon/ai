"""⑧ message notes: which notices go with a verdict (docs/8-agent-xai.md §4 `notes`, §4-1).

Rules only, no LLM. This picks the note codes and what each one is about; the
fixed text for each locale is in ``messages.py``. Notes never change the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_supervisor.contracts.common import AmbiguityFlag, LocalizedIngredientRef

from .policy import DecisionOutcome

NoteCode = Literal["ambiguous_menu", "hard_to_remove"]
# identity: the dish is built on it. hidden: it sits inside a sauce or seasoning (§4-1).
HARD_TO_REMOVE_ROLES = frozenset({"identity", "hidden"})


@dataclass(frozen=True)
class NoteChoice:
    code: NoteCode
    ambiguity_flag: AmbiguityFlag | None
    ingredient: LocalizedIngredientRef | None
    role: Literal["identity", "hidden"] | None


COVERED_BY_HIDDEN_ANIMAL = frozenset({"has_unclear_broth", "has_unclear_seasoning"})


def select_notes(outcome: DecisionOutcome) -> list[NoteChoice]:
    """``ambiguous_menu`` for each related flag the owner has not settled, then
    ``hard_to_remove`` for each risk ingredient whose role is identity or hidden,
    in ``risk_ingredients`` order (already one per ingredient, see ``merge_same_ingredient``).
    A missing role gets no note: we do not guess."""

    unresolved = [check for check in outcome.flag_checks if check.state == "unresolved"]
    # has_hidden_animal's place is "sauces, seasoning or broth", so a broth or seasoning
    # note about the same restrictions only says it again (2026-11-12, "…in the sauces,
    # seasoning or broth" next to "…in the broth").
    wide = {
        tag
        for check in unresolved
        if check.flag == "has_hidden_animal"
        for tag in check.ask_tags
    }
    notes = [
        NoteChoice("ambiguous_menu", check.flag, None, None)
        for check in unresolved
        if not (check.flag in COVERED_BY_HIDDEN_ANIMAL and set(check.ask_tags) <= wide)
    ]
    notes += [
        NoteChoice("hard_to_remove", None, item.ingredient, item.role)
        for item in outcome.risk_ingredients
        if item.role in HARD_TO_REMOVE_ROLES
    ]
    return notes
