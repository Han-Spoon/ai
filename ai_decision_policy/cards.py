"""⑧ owner question cards (docs/8-agent-xai.md §1-3, §2-1 step 7; ⓪ §4-7).

Rules only, no LLM. A card goes out for every ingredient that overlaps the user's
forbidden tags and has no owner answer yet, whatever its probability. The owner is
asked once per target: an answered ingredient is never asked again, even when its
answer was flagged as anomalous (10/26 meeting, §1-3). Unresolved menu ambiguity flags get
one card each, and a menu with no information gets one display-only card that asks
about every forbidden tag.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_supervisor.contracts.common import IngredientRef
from ai_supervisor.contracts.decision import DecisionRequest
from ai_supervisor.contracts.external import OwnerCard

from .policy import DecisionOutcome, has_no_ingredients
from .questions import flag_question, ingredient_question, menu_question

# Card order: reasons whose answer is most likely to change the verdict first (§1-3).
REASON_PRIORITY = (
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


def _ordered_reasons(reasons: set[str]) -> list[str]:
    ordered = [r for r in REASON_PRIORITY if r in reasons]
    return ordered or ["unconfirmed"]


def _card_id(item_id: str, target: str) -> str:
    return f"card:{item_id}:{target}"


def _ingredient_target(ingredient: IngredientRef) -> str:
    if ingredient.ingredient_id is not None:
        return f"ingredient-{ingredient.ingredient_id}"
    return f"name-{ingredient.canonical_name}"


def _collect_ingredients(
    request: DecisionRequest, forbidden: frozenset[str]
) -> list[_IngredientTarget]:
    # present/absent is an owner answer whether or not it may override; unknown is not.
    answered = {
        _key(c.ingredient)
        for c in request.confirmed_results
        if c.status in ("present", "absent")
    }
    targets: dict[tuple[str, int | str], _IngredientTarget] = {}

    def target(ingredient: IngredientRef, tags: list[str], refs: list[str]) -> _IngredientTarget:
        entry = targets.setdefault(_key(ingredient), _IngredientTarget(ingredient))
        entry.tags.update(tags)
        entry.evidence_refs.extend(refs)
        return entry

    for c in request.confirmed_results:
        if _key(c.ingredient) not in answered:
            target(c.ingredient, c.constraint_tags, c.evidence_refs)
    for p in request.probability_results:
        if _key(p.ingredient) in answered:
            continue
        entry = target(p.ingredient, p.constraint_tags, p.evidence_refs)
        if p.confidence is not None and p.confidence < LOW_CONFIDENCE:
            entry.reasons.add("insufficient_recipe_evidence")
        if entry.posterior_mean is None or p.posterior_mean > entry.posterior_mean:
            entry.posterior_mean = p.posterior_mean
    for v in request.proposed_variant_ingredients:
        if _key(v.ingredient) not in answered:
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

    # A menu with no information has no ingredient list to ask about: one display-only
    # card for the whole menu (§1-3). ``round`` stays 1 on every card until the ⓪
    # contract code drops it (§9).
    if request.no_information or has_no_ingredients(request):
        tags = sorted(forbidden)
        return [
            OwnerCard(
                item_id=item_id,
                card_id=_card_id(item_id, "menu"),
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
        card_id = _card_id(item_id, _ingredient_target(t.ingredient))
        ingredient_id = t.ingredient.ingredient_id
        localized = names.get(ingredient_id, t.ingredient.canonical_name)
        posterior[card_id] = t.posterior_mean
        cards.append(
            OwnerCard(
                item_id=item_id,
                card_id=card_id,
                menu_id=menu_id,
                round=1,
                previous_question_id=None,
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
        tags = check.ask_tags
        target = f"flag-{check.flag}-{'+'.join(tags)}"
        cards.append(
            OwnerCard(
                item_id=item_id,
                card_id=_card_id(item_id, target),
                menu_id=menu_id,
                round=1,
                previous_question_id=None,
                ingredient=None,
                ambiguity_flag=check.flag,
                constraint_tags=tags,
                question=flag_question(check.flag, tags, locale),
                reason_codes=["ambiguous_menu"],
                evidence_refs=list(dict.fromkeys(a.evidence.evidence_id for a in answers)),
            )
        )

    return sorted(cards, key=lambda card: _sort_key(card, posterior))
