"""⑧ Decision Policy: DANGER / CAUTION / SAFE from rules only (docs/8-agent-xai.md §2, §3).

No LLM here. The same request always gives the same verdict. Message text and owner
cards are built on top of ``DecisionOutcome`` later.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_supervisor.contracts.common import (
    IngredientRef,
    IngredientStatus,
    LocalizedIngredientRef,
)
from ai_supervisor.contracts.decision import (
    ConfirmedResult,
    DecisionProbabilityResult,
    DecisionRequest,
    DecisionRiskIngredient,
    EvidenceBasis,
    HighRiskReview,
    InformationStatus,
    ProposedVariantIngredient,
    ReviewReason,
    RiskLevel,
)

from .flags import FlagCheck, check_flag
from .names import display_name
from .profile import forbidden_tags

# High-risk review signals (§2-3). Order is the priority when several apply.
REVIEW_PRIORITY: tuple[ReviewReason, ...] = (
    "unknown_tag",
    "no_information",
    "partial_information",
    "insufficient_web_sources",
)
UNKNOWN_TAG_WARNINGS = frozenset({"unmapped_constraint_tags", "constraint_tags_lookup_failed"})
WEB_SHORTAGE_WARNINGS = frozenset(
    {"insufficient_sources", "web_search_deadline_exceeded", "web_llm_extraction_failed"}
)
# An estimate under this is not shown (2026-11-25, ⑧ 담당): not in ``risk_ingredients``,
# the verdict names, the notes or the owner cards. 비건 김치찌개 listed 생선 2.2% next to
# 멸치 97%, and rows of 1~4% filled the screen. It still counts for the verdict: any
# estimate keeps the menu at CAUTION (§2-1 step 4).
SHOWN_MIN_CHANCE = 0.05


def is_low_chance(item: DecisionProbabilityResult) -> bool:
    return item.posterior_mean < SHOWN_MIN_CHANCE


@dataclass(frozen=True)
class DecisionOutcome:
    risk_level: RiskLevel
    evidence_basis: EvidenceBasis
    information_status: InformationStatus
    forbidden_tags: list[str]
    risk_ingredients: list[DecisionRiskIngredient]
    matched_tags: list[str]
    evidence_refs: list[str]
    flag_checks: list[FlagCheck]
    high_risk_review: HighRiskReview
    # Tags in ``matched_tags`` that only those hidden estimates carry. ``matched_tags``
    # keeps them (detection, QA B25: every allergen in a recipe is caught), but the
    # verdict must not name them as a menu attribute.
    low_chance_tags: tuple[str, ...] = ()


def _key(ingredient: IngredientRef) -> tuple[str, int | str]:
    if ingredient.ingredient_id is not None:
        return ("id", ingredient.ingredient_id)
    return ("name", ingredient.canonical_name)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _stronger(a: DecisionRiskIngredient, b: DecisionRiskIngredient) -> bool:
    """An owner "present" first, then a known probability over none, then the higher one."""

    if (a.status == "present") != (b.status == "present"):
        return a.status == "present"
    if b.posterior_mean is None:
        return a.posterior_mean is not None
    return a.posterior_mean is not None and a.posterior_mean > b.posterior_mean


def merge_same_ingredient(items: list[DecisionRiskIngredient]) -> list[DecisionRiskIngredient]:
    """One entry per ingredient. ④ sends one row per tag bundle (#327), so 액젓 can come
    as is_fish and again as is_fish + is_shellfish. The stronger row stays and the tags
    and evidence of all rows are joined, so no matched tag is lost (§1-2)."""

    merged: dict[tuple[str, int | str], DecisionRiskIngredient] = {}
    for item in items:
        key = _key(item.ingredient)
        seen = merged.get(key)
        if seen is None:
            merged[key] = item
            continue
        base, other = (item, seen) if _stronger(item, seen) else (seen, item)
        merged[key] = base.model_copy(
            update={
                "constraint_tags": _dedupe([*base.constraint_tags, *other.constraint_tags]),
                "matched_tags": sorted({*base.matched_tags, *other.matched_tags}),
                "evidence_refs": _dedupe([*base.evidence_refs, *other.evidence_refs]),
            }
        )
    return list(merged.values())


def sort_by_risk(items: list[DecisionRiskIngredient]) -> list[DecisionRiskIngredient]:
    """Owner "present" first, then the higher probability, then rows with no number
    (variant guesses) last; ties keep their order (2026-11-23, §1-2). The screen lists
    ``risk_ingredients`` as sent, so the likeliest cause comes first."""

    return sorted(
        items,
        key=lambda i: (
            i.status != "present",
            i.posterior_mean is None,
            -(i.posterior_mean or 0.0),
        ),
    )


def is_hard_evidence(result: ConfirmedResult) -> bool:
    """Every present/absent owner answer counts; ``unknown`` never means "absent" (§1-1).

    The legacy ``override_eligible`` flag is ignored: owner answers always override
    (10/27 meeting, ⓪ #368), so an omitted or false value changes nothing.
    """

    return result.status in ("present", "absent")


def has_no_ingredients(request: DecisionRequest) -> bool:
    return not (
        request.confirmed_results
        or request.probability_results
        or request.proposed_variant_ingredients
    )


def information_failed(request: DecisionRequest) -> bool:
    """``information_status: partial`` that means something failed to load (2026-11-17).

    ⓪ also sends ``partial`` when a warning forces caution, e.g. only a few web recipes
    or an ingredient with unknown tags. Those warnings say what happened themselves, so
    "part of the information could not be loaded" would be untrue: it showed on almost
    every web menu in the 2026-11-12~17 runs, and the review reason became
    ``partial_information`` instead of ``insufficient_web_sources``. Errors, or
    ``partial`` with no such warning, still count.
    """

    if request.information_status != "partial":
        return False
    if request.errors:
        return True
    return not any(
        w.forces_caution and w.code != "store_context_missing" for w in request.warnings
    )


def high_risk_review(request: DecisionRequest, forbidden: frozenset[str]) -> HighRiskReview:
    """Mark whether this menu should get the high-risk review (§2-3).

    Only signals already in the request are used, and only when they matter to this
    user. Unknown tags always count because we cannot tell whether they overlap.
    """

    if not request.owner_interaction_allowed:
        return HighRiskReview(required=False, reason_code=None)

    codes = {w.code for w in request.warnings}
    signals: set[ReviewReason] = set()
    if codes & UNKNOWN_TAG_WARNINGS:
        signals.add("unknown_tag")
    if forbidden:
        # No ingredients at all is treated like no_information, as in §2-1 step 3.
        if request.no_information or has_no_ingredients(request):
            signals.add("no_information")
        if information_failed(request):
            signals.add("partial_information")
        if codes & WEB_SHORTAGE_WARNINGS:
            signals.add("insufficient_web_sources")

    for reason in REVIEW_PRIORITY:
        if reason in signals:
            return HighRiskReview(required=True, reason_code=reason)
    return HighRiskReview(required=False, reason_code=None)


def decide(request: DecisionRequest) -> DecisionOutcome:
    forbidden = forbidden_tags(request.user_profile)
    names = {n.ingredient_id: n.localized_name for n in request.localized_names}
    hard = [c for c in request.confirmed_results if is_hard_evidence(c)]
    hard_keys = {_key(c.ingredient) for c in hard}
    # An eligible owner answer beats the probability for the same ingredient (§6).
    probabilities = [
        p for p in request.probability_results if _key(p.ingredient) not in hard_keys
    ]
    variants = request.proposed_variant_ingredients

    def risk(
        item: ConfirmedResult | DecisionProbabilityResult | ProposedVariantIngredient,
        status: IngredientStatus,
    ) -> DecisionRiskIngredient | None:
        matched = sorted(set(item.constraint_tags) & forbidden)
        if not matched:
            return None
        probability = item if isinstance(item, DecisionProbabilityResult) else None
        ingredient_id = item.ingredient.ingredient_id
        return DecisionRiskIngredient(
            # localized_names first, then the team's tables, else Korean (§1-2, names.py).
            ingredient=LocalizedIngredientRef(
                ingredient_id=ingredient_id,
                canonical_name=item.ingredient.canonical_name,
                localized_name=display_name(item.ingredient, names, request.locale),
            ),
            constraint_tags=list(item.constraint_tags),
            matched_tags=matched,
            role=item.role,
            status=status,
            posterior_mean=probability.posterior_mean if probability else None,
            confidence=probability.confidence if probability else None,
            prior_source=probability.prior_source if probability else None,
            evidence_refs=list(item.evidence_refs),
        )

    # Rows under 5% are dropped before rows of the same ingredient are merged, so a 3%
    # tag bundle (김치 속 액젓의 새우) does not ride on the 60% row's number.
    shown = [p for p in probabilities if not is_low_chance(p)]
    hidden_tags = {
        t for p in probabilities if is_low_chance(p) for t in set(p.constraint_tags) & forbidden
    }
    candidates = (
        [risk(c, c.status) for c in hard if c.status == "present"]
        + [risk(p, "unknown") for p in shown]
        + [risk(v, "unknown") for v in variants]
    )
    risk_ingredients = sort_by_risk(
        merge_same_ingredient([r for r in candidates if r is not None])
    )

    observed = [o for o in request.risk_tag_observations if o.tag in forbidden]
    flag_checks = [
        check
        for flag in request.menu_ambiguity_flags
        if (check := check_flag(flag, forbidden, request.flag_confirmations)) is not None
    ]
    danger_flags = [f for f in flag_checks if f.state == "danger"]

    shown_tags = (
        {t for r in risk_ingredients for t in r.matched_tags}
        | {o.tag for o in observed}
        | {t for f in danger_flags for t in f.matched_tags}
    )
    # Hidden estimates stay in the detected tags; only the screen leaves them out.
    matched_tags = sorted(shown_tags | hidden_tags)
    low_chance_tags = tuple(sorted(hidden_tags - shown_tags))
    evidence_refs = _dedupe(
        [ref for c in hard for ref in c.evidence_refs]
        + [ref for p in probabilities for ref in p.evidence_refs]
        + [ref for v in variants for ref in v.evidence_refs]
        + [ref for o in observed for ref in o.evidence_refs]
    )
    review = high_risk_review(request, forbidden)

    def outcome(level: RiskLevel, basis: EvidenceBasis) -> DecisionOutcome:
        return DecisionOutcome(
            risk_level=level,
            evidence_basis=basis,
            information_status=request.information_status,
            forbidden_tags=sorted(forbidden),
            risk_ingredients=risk_ingredients,
            matched_tags=matched_tags,
            evidence_refs=evidence_refs,
            flag_checks=flag_checks,
            high_risk_review=review,
            low_chance_tags=low_chance_tags,
        )

    # Step 2: an owner-confirmed forbidden ingredient (or flag answer) is DANGER at once.
    confirmed_danger = any(r.status == "present" for r in risk_ingredients)
    if confirmed_danger or danger_flags:
        return outcome("danger", "confirmed")

    # Step 3: no information, or nothing to judge, is never SAFE (2026-10-18).
    if request.no_information or not (request.confirmed_results or probabilities or variants):
        return outcome("caution", "unknown")

    # Step 4: anything resting on probability or variant guesses stays CAUTION (§3).
    if probabilities or variants:
        return outcome("caution", "estimated")

    # Step 4-1 / 4-2: every ingredient is an owner answer; check the remaining SAFE blocks.
    missing_information = (
        request.information_status != "complete"
        or any(w.forces_caution for w in request.warnings)
        or not request.owner_interaction_allowed
        or any(f.state == "unresolved" for f in flag_checks)
        or any(not is_hard_evidence(c) for c in request.confirmed_results)
    )
    if missing_information:
        return outcome("caution", "unknown")
    if observed:
        return outcome("caution", "estimated")
    return outcome("safe", "confirmed")
