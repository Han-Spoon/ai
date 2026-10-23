"""⑧ owner question cards (docs/8-agent-xai.md §1-3, §2-1 step 7; ⓪ §4-7).

Rules only, no LLM. A card goes out for every ingredient that overlaps the user's
forbidden tags and has no usable owner answer, whatever its probability. Unresolved
menu ambiguity flags get one card each, and a menu with no information gets one
display-only card that asks about every forbidden tag.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_supervisor.contracts.common import IngredientRef, OwnerQuestionState
from ai_supervisor.contracts.decision import DecisionRequest
from ai_supervisor.contracts.external import OwnerCard

from .policy import DecisionOutcome, has_no_ingredients, is_hard_evidence
from .questions import flag_question, ingredient_question, menu_question

# Card order: reasons whose answer is most likely to change the verdict first (§1-3).
REASON_PRIORITY = (
    "anomaly",
    "expired",
    "ambiguous_menu",
    "high_risk_review",
    "insufficient_recipe_evidence",
    "no_information",
    "unconfirmed",
)
# Fewer than 5 real recipe sources (⑤ §1-2-1). A display reason only, never a filter.
LOW_CONFIDENCE = 0.59


def _key(ingredient: IngredientRef) -> tuple[str, int | str]:
    if ingredient.ingredient_id is not None:
        return ("id", ingredient.ingredient_id)
    return ("name", ingredient.canonical_name)


@dataclass
class _IngredientTarget:
    ingredient: IngredientRef
    tags: set[str] = field(default_factory=set)
    reasons: set[str] = field(default_factory=set)
    posterior_mean: float | None = None
    evidence_refs: list[str] = field(default_factory=list)


def _round(
    state: OwnerQuestionState | None,
) -> tuple[int, str | None]:
    """needs_second → 2, needs_third → 3. No state or ``confirmed`` starts a new round 1:
    a card for a confirmed target only exists because that answer expired or was
    flagged, so it is a new confirmation cycle (§1-3, ⓪ §4-7)."""

    if state is None or state.confirmation_state == "confirmed":
        return 1, None
    if state.confirmation_state == "needs_second":
        return 2, state.question_id
    return 3, state.question_id


def _ingredient_state(
    states: list[OwnerQuestionState], ingredient: IngredientRef
) -> OwnerQuestionState | None:
    key = _key(ingredient)
    return next(
        (s for s in states if s.ingredient is not None and _key(s.ingredient) == key), None
    )


def _flag_state(
    states: list[OwnerQuestionState], flag: str, tags: list[str]
) -> OwnerQuestionState | None:
    return next(
        (
            s
            for s in states
            if s.ambiguity_flag == flag and sorted(s.constraint_tags) == tags
        ),
        None,
    )


def _ordered_reasons(reasons: set[str]) -> list[str]:
    ordered = [r for r in REASON_PRIORITY if r in reasons]
    return ordered or ["unconfirmed"]


def _card_id(item_id: str, target: str, round_: int) -> str:
    return f"card:{item_id}:{target}:r{round_}"


def _ingredient_target(ingredient: IngredientRef) -> str:
    if ingredient.ingredient_id is not None:
        return f"ingredient-{ingredient.ingredient_id}"
    return f"name-{ingredient.canonical_name}"


def _collect_ingredients(
    request: DecisionRequest, forbidden: frozenset[str]
) -> list[_IngredientTarget]:
    hard_keys = {_key(c.ingredient) for c in request.confirmed_results if is_hard_evidence(c)}
    targets: dict[tuple[str, int | str], _IngredientTarget] = {}

    def target(ingredient: IngredientRef, tags: list[str], refs: list[str]) -> _IngredientTarget:
        entry = targets.setdefault(_key(ingredient), _IngredientTarget(ingredient))
        entry.tags.update(tags)
        entry.evidence_refs.extend(refs)
        return entry

    for c in request.confirmed_results:
        if is_hard_evidence(c):
            continue
        entry = target(c.ingredient, c.constraint_tags, c.evidence_refs)
        if c.expired:
            entry.reasons.add("expired")
    for p in request.probability_results:
        if _key(p.ingredient) in hard_keys:
            continue
        entry = target(p.ingredient, p.constraint_tags, p.evidence_refs)
        if p.anomaly_locked:
            entry.reasons.add("anomaly")
        if p.confidence is not None and p.confidence < LOW_CONFIDENCE:
            entry.reasons.add("insufficient_recipe_evidence")
        if entry.posterior_mean is None or p.posterior_mean > entry.posterior_mean:
            entry.posterior_mean = p.posterior_mean
    for v in request.proposed_variant_ingredients:
        if _key(v.ingredient) not in hard_keys:
            target(v.ingredient, v.constraint_tags, v.evidence_refs)

    # Ingredients whose tags ④ could not resolve have no overlap and get no card (§1-3).
    return [t for t in targets.values() if t.tags & forbidden]


def _sort_key(card: OwnerCard, posterior: dict[str, float | None]) -> tuple:
    first_reason = min(REASON_PRIORITY.index(r) for r in card.reason_codes)
    if card.ingredient is not None:
        mean = posterior.get(card.card_id)
        if mean is not None:
            return (first_reason, 0, -mean, "")
        ingredient_id = card.ingredient.ingredient_id
        return (first_reason, 1, ingredient_id or float("inf"), card.ingredient.canonical_name)
    return (first_reason, 2, 0, card.ambiguity_flag or "")


def build_owner_cards(request: DecisionRequest, outcome: DecisionOutcome) -> list[OwnerCard]:
    if not request.owner_interaction_allowed:
        return []
    forbidden = frozenset(outcome.forbidden_tags)
    if not forbidden:
        return []

    item_id = request.context.item_id
    menu_id = request.menu.menu_id
    locale = request.locale
    names = {n.ingredient_id: n.localized_name for n in request.localized_names}
    states = request.owner_question_states

    # A menu with no information has no ingredient list to ask about: one display-only
    # card for the whole menu. It is never stored, so it is always round 1 (§1-3).
    if request.no_information or has_no_ingredients(request):
        tags = sorted(forbidden)
        return [
            OwnerCard(
                item_id=item_id,
                card_id=_card_id(item_id, "menu", 1),
                menu_id=menu_id,
                round=1,
                previous_question_id=None,
                ingredient=None,
                ambiguity_flag=None,
                constraint_tags=tags,
                question=menu_question(tags, locale),
                reason_codes=["no_information"],
                evidence_refs=[],
            )
        ]

    cards: list[OwnerCard] = []
    posterior: dict[str, float | None] = {}
    for t in _collect_ingredients(request, forbidden):
        round_, previous = _round(_ingredient_state(states, t.ingredient))
        card_id = _card_id(item_id, _ingredient_target(t.ingredient), round_)
        ingredient_id = t.ingredient.ingredient_id
        localized = names.get(ingredient_id, t.ingredient.canonical_name)
        posterior[card_id] = t.posterior_mean
        cards.append(
            OwnerCard(
                item_id=item_id,
                card_id=card_id,
                menu_id=menu_id,
                round=round_,
                previous_question_id=previous,
                ingredient=t.ingredient,
                ambiguity_flag=None,
                constraint_tags=[],
                question=ingredient_question(t.ingredient.canonical_name, localized, locale),
                reason_codes=_ordered_reasons(t.reasons),
                evidence_refs=list(dict.fromkeys(t.evidence_refs)),
            )
        )

    for check in outcome.flag_checks:
        if check.state != "unresolved":
            continue
        answers = [a for a in request.flag_confirmations if a.ambiguity_flag == check.flag]
        reasons = {"ambiguous_menu"}
        if any(a.expired for a in answers):
            reasons.add("expired")
        tags = check.ask_tags
        round_, previous = _round(_flag_state(states, check.flag, tags))
        target = f"flag-{check.flag}-{'+'.join(tags)}"
        cards.append(
            OwnerCard(
                item_id=item_id,
                card_id=_card_id(item_id, target, round_),
                menu_id=menu_id,
                round=round_,
                previous_question_id=previous,
                ingredient=None,
                ambiguity_flag=check.flag,
                constraint_tags=tags,
                question=flag_question(check.flag, tags, locale),
                reason_codes=_ordered_reasons(reasons),
                evidence_refs=list(dict.fromkeys(a.evidence.evidence_id for a in answers)),
            )
        )

    return sorted(cards, key=lambda card: _sort_key(card, posterior))
