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


def select_notes(outcome: DecisionOutcome) -> list[NoteChoice]:
    """``ambiguous_menu`` for each related flag the owner has not settled, then
    ``hard_to_remove`` for each risk ingredient whose role is identity or hidden,
    in ``risk_ingredients`` order. A missing role gets no note: we do not guess."""

    notes = [
        NoteChoice("ambiguous_menu", check.flag, None, None)
        for check in outcome.flag_checks
        if check.state == "unresolved"
    ]
    seen: set[tuple[str, int | str]] = set()
    for item in outcome.risk_ingredients:
        if item.role not in HARD_TO_REMOVE_ROLES:
            continue
        ingredient = item.ingredient
        key = (
            ("id", ingredient.ingredient_id)
            if ingredient.ingredient_id is not None
            else ("name", ingredient.canonical_name)
        )
        if key in seen:
            continue
        seen.add(key)
        notes.append(NoteChoice("hard_to_remove", None, ingredient, item.role))
    return notes
