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
from .profile import forbidden_tags

# High-risk review signals (§2-3). Order is the priority when several apply.
REVIEW_PRIORITY: tuple[ReviewReason, ...] = (
    "anomaly",
    "unknown_tag",
    "no_information",
    "partial_information",
    "insufficient_web_sources",
)
UNKNOWN_TAG_WARNINGS = frozenset({"unmapped_constraint_tags", "constraint_tags_lookup_failed"})
WEB_SHORTAGE_WARNINGS = frozenset(
    {"insufficient_sources", "web_search_deadline_exceeded", "web_llm_extraction_failed"}
)


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


def _key(ingredient: IngredientRef) -> tuple[str, int | str]:
    if ingredient.ingredient_id is not None:
        return ("id", ingredient.ingredient_id)
    return ("name", ingredient.canonical_name)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def is_hard_evidence(result: ConfirmedResult) -> bool:
    """Only eligible present/absent answers count; ``unknown`` never means "absent" (§1-1)."""

    return result.override_eligible and result.status in ("present", "absent")


def has_no_ingredients(request: DecisionRequest) -> bool:
    return not (
        request.confirmed_results
        or request.probability_results
        or request.proposed_variant_ingredients
    )


def high_risk_review(request: DecisionRequest, forbidden: frozenset[str]) -> HighRiskReview:
    """Mark whether this menu should get the expensive review (§2-3).

    Only signals already in the request are used, and only when they matter to this
    user: menu-level gaps need some forbidden tag, an anomaly needs an overlapping tag.
    Unknown tags always count because we cannot tell whether they overlap.
    """

    if not request.owner_interaction_allowed:
        return HighRiskReview(required=False, reason_code=None)

    codes = {w.code for w in request.warnings}
    signals: set[ReviewReason] = set()
    if any(
        p.anomaly_locked and set(p.constraint_tags) & forbidden
        for p in request.probability_results
    ):
        signals.add("anomaly")
    if codes & UNKNOWN_TAG_WARNINGS:
        signals.add("unknown_tag")
    if forbidden:
        # No ingredients at all is treated like no_information, as in §2-1 step 3.
        if request.no_information or has_no_ingredients(request):
            signals.add("no_information")
        if request.information_status != "complete":
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
            # A missing translation keeps the Korean name; a wrong one is worse (§1-2).
            ingredient=LocalizedIngredientRef(
                ingredient_id=ingredient_id,
                canonical_name=item.ingredient.canonical_name,
                localized_name=names.get(ingredient_id, item.ingredient.canonical_name),
            ),
            constraint_tags=list(item.constraint_tags),
            matched_tags=matched,
            role=item.role,
            status=status,
            posterior_mean=probability.posterior_mean if probability else None,
            confidence=probability.confidence if probability else None,
            prior_source=probability.prior_source if probability else None,
            anomaly_locked=probability.anomaly_locked if probability else False,
            evidence_refs=list(item.evidence_refs),
        )

    candidates = (
        [risk(c, c.status) for c in hard if c.status == "present"]
        + [risk(p, "unknown") for p in probabilities]
        + [risk(v, "unknown") for v in variants]
    )
    risk_ingredients = [r for r in candidates if r is not None]

    observed = [o for o in request.risk_tag_observations if o.tag in forbidden]
    flag_checks = [
        check
        for flag in request.menu_ambiguity_flags
        if (check := check_flag(flag, forbidden, request.flag_confirmations)) is not None
    ]
    danger_flags = [f for f in flag_checks if f.state == "danger"]

    matched_tags = sorted(
        {t for r in risk_ingredients for t in r.matched_tags}
        | {o.tag for o in observed}
        | {t for f in danger_flags for t in f.matched_tags}
    )
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
