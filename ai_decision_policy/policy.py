"""⑧ Decision Policy: DANGER / CAUTION / SAFE from rules only (docs/8-agent-xai.md §2, §3).

No LLM here. The same input always gives the same verdict. Message text, owner cards
and the high-risk review flag are built on top of ``DecisionOutcome`` later, and the
input dataclasses are swapped for the ⓪ §4-7 wire models once those exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ai_supervisor.contracts.common import (
    AmbiguityFlag,
    FlagConfirmation,
    IngredientRef,
    IngredientStatus,
    NodeWarning,
    UserProfile,
)

from .flags import FlagCheck, check_flag
from .profile import forbidden_tags

RiskLevel = Literal["danger", "caution", "safe"]
EvidenceBasis = Literal["confirmed", "estimated", "unknown"]
InformationStatus = Literal["complete", "partial", "none"]
Role = Literal["identity", "component", "hidden"]


@dataclass(frozen=True)
class ConfirmedResult:
    ingredient: IngredientRef
    constraint_tags: list[str]
    role: Role | None
    status: IngredientStatus
    override_eligible: bool
    expired: bool
    evidence_refs: list[str]


@dataclass(frozen=True)
class ProbabilityResult:
    ingredient: IngredientRef
    constraint_tags: list[str]
    role: Role | None
    posterior_mean: float
    confidence: float | None
    prior_source: Literal["store", "cluster", "global", "uninformative"] | None
    anomaly_locked: bool
    evidence_refs: list[str]


@dataclass(frozen=True)
class VariantIngredient:
    ingredient: IngredientRef
    constraint_tags: list[str]
    role: Role | None
    evidence_refs: list[str]


@dataclass(frozen=True)
class TagObservation:
    tag: str
    confidence: float
    evidence_refs: list[str]


@dataclass(frozen=True)
class DecisionInput:
    user_profile: UserProfile
    confirmed_results: list[ConfirmedResult] = field(default_factory=list)
    probability_results: list[ProbabilityResult] = field(default_factory=list)
    proposed_variant_ingredients: list[VariantIngredient] = field(default_factory=list)
    risk_tag_observations: list[TagObservation] = field(default_factory=list)
    menu_ambiguity_flags: list[AmbiguityFlag] = field(default_factory=list)
    flag_confirmations: list[FlagConfirmation] = field(default_factory=list)
    no_information: bool = False
    information_status: InformationStatus = "complete"
    owner_interaction_allowed: bool = True
    localized_names: dict[int, str] = field(default_factory=dict)
    warnings: list[NodeWarning] = field(default_factory=list)


@dataclass(frozen=True)
class RiskIngredient:
    ingredient: IngredientRef
    localized_name: str
    constraint_tags: list[str]
    matched_tags: list[str]
    role: Role | None
    status: IngredientStatus
    posterior_mean: float | None
    confidence: float | None
    prior_source: str | None
    anomaly_locked: bool
    evidence_refs: list[str]


@dataclass(frozen=True)
class DecisionOutcome:
    risk_level: RiskLevel
    evidence_basis: EvidenceBasis
    information_status: InformationStatus
    forbidden_tags: list[str]
    risk_ingredients: list[RiskIngredient]
    matched_tags: list[str]
    evidence_refs: list[str]
    flag_checks: list[FlagCheck]


def _key(ingredient: IngredientRef) -> tuple[str, int | str]:
    if ingredient.ingredient_id is not None:
        return ("id", ingredient.ingredient_id)
    return ("name", ingredient.canonical_name)


def _localized(ingredient: IngredientRef, names: dict[int, str]) -> str:
    # A missing translation keeps the Korean name; a wrong translation is worse (§1-2).
    if ingredient.ingredient_id is not None and ingredient.ingredient_id in names:
        return names[ingredient.ingredient_id]
    return ingredient.canonical_name


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def is_hard_evidence(result: ConfirmedResult) -> bool:
    """Only eligible present/absent answers count; ``unknown`` never means "absent" (§1-1)."""

    return result.override_eligible and result.status in ("present", "absent")


def decide(data: DecisionInput) -> DecisionOutcome:
    forbidden = forbidden_tags(data.user_profile)
    hard = [c for c in data.confirmed_results if is_hard_evidence(c)]
    hard_keys = {_key(c.ingredient) for c in hard}
    # An eligible owner answer beats the probability for the same ingredient (§6).
    probabilities = [
        p for p in data.probability_results if _key(p.ingredient) not in hard_keys
    ]
    variants = data.proposed_variant_ingredients

    def risk(
        item: ConfirmedResult | ProbabilityResult | VariantIngredient,
        status: IngredientStatus,
        probability: ProbabilityResult | None = None,
    ) -> RiskIngredient | None:
        matched = sorted(set(item.constraint_tags) & forbidden)
        if not matched:
            return None
        return RiskIngredient(
            ingredient=item.ingredient,
            localized_name=_localized(item.ingredient, data.localized_names),
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
        + [risk(p, "unknown", p) for p in probabilities]
        + [risk(v, "unknown") for v in variants]
    )
    risk_ingredients = [r for r in candidates if r is not None]

    observed = [o for o in data.risk_tag_observations if o.tag in forbidden]
    flag_checks = [
        check
        for flag in data.menu_ambiguity_flags
        if (check := check_flag(flag, forbidden, data.flag_confirmations)) is not None
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

    def outcome(level: RiskLevel, basis: EvidenceBasis) -> DecisionOutcome:
        return DecisionOutcome(
            risk_level=level,
            evidence_basis=basis,
            information_status=data.information_status,
            forbidden_tags=sorted(forbidden),
            risk_ingredients=risk_ingredients,
            matched_tags=matched_tags,
            evidence_refs=evidence_refs,
            flag_checks=flag_checks,
        )

    # Step 2: an owner-confirmed forbidden ingredient (or flag answer) is DANGER at once.
    confirmed_danger = any(r.status == "present" for r in risk_ingredients)
    if confirmed_danger or danger_flags:
        return outcome("danger", "confirmed")

    # Step 3: no information, or nothing to judge, is never SAFE (2026-10-18).
    if data.no_information or not (data.confirmed_results or probabilities or variants):
        return outcome("caution", "unknown")

    # Step 4: anything resting on probability or variant guesses stays CAUTION (§3).
    if probabilities or variants:
        return outcome("caution", "estimated")

    # Step 4-1 / 4-2: every ingredient is an owner answer; check the remaining SAFE blocks.
    missing_information = (
        data.information_status != "complete"
        or any(w.forces_caution for w in data.warnings)
        or not data.owner_interaction_allowed
        or any(f.state == "unresolved" for f in flag_checks)
        or any(not is_hard_evidence(c) for c in data.confirmed_results)
    )
    if missing_information:
        return outcome("caution", "unknown")
    if observed:
        return outcome("caution", "estimated")
    return outcome("safe", "confirmed")
