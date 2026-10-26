"""⑤ Bayesian Tool: Beta-Binomial probability per unconfirmed ingredient.

docs/5-tool-Statistics.md §1-2, §2, §3. Pure function: same input, same output, no DB,
no LLM, no verdict.
"""

from __future__ import annotations

import math

from ai_supervisor.contracts.bayesian import (
    BayesianIngredient,
    BayesianRequest,
    BayesianResponse,
    ProbabilityResult,
)
from ai_supervisor.contracts.common import NodeWarning
from ai_supervisor.contracts.external import StorePrior

NODE = "bayesian"
A0, B0 = 1.0, 1.0  # Beta(1,1) Laplace smoothing, same as crawling/normalize_ingredients.py
UNKNOWN_SOURCE_WEIGHT = 0.5  # (0+1)/(0+2) for a source never compared yet (⑦ §3-1)
VARIANT_SCALE = 0.5
DEPTH_SCALE_FLOOR = 0.125


def beta_binomial_update(
    k_count: float, n_total: float, a0: float = A0, b0: float = B0
) -> tuple[float, float]:
    """α = k + a0, β = (n − k) + b0 (§3)."""

    return k_count + a0, (n_total - k_count) + b0


def source_scale(ingredient: BayesianIngredient) -> float:
    """§2-1: shrink the final α·β by the same ratio. Mean is kept, spread grows."""

    if ingredient.source == "variant_suggested":
        return VARIANT_SCALE
    if ingredient.source == "expanded" and ingredient.observation_basis != "direct":
        return max(0.5**ingredient.depth, DEPTH_SCALE_FLOOR)
    return 1.0  # recipe, web_search (already discounted by reliability), direct sub-ingredient


def confidence_score(alpha: float, beta: float) -> float:
    """§1-2-1: 1 − 1/√(ν+1), ν = effective evidence count."""

    nu = max(alpha + beta - A0 - B0, 0.0)
    return 1.0 - 1.0 / math.sqrt(nu + 1.0)


class _Weights:
    def __init__(self, request: BayesianRequest) -> None:
        self._weights = {w.source_key: w.weight for w in request.source_reliability.weights}

    def of(self, key: str) -> float:
        return self._weights.get(key, UNKNOWN_SOURCE_WEIGHT)


def _store_priors(request: BayesianRequest) -> dict[int, StorePrior]:
    """Store evidence (`ingredient_risk_scores`). Kept apart so it can be dropped if the
    team removes store prior; storeless scans never use it."""

    if request.context.store_id is None or request.priors.status != "ok":
        return {}
    return {prior.ingredient_id: prior for prior in request.priors.store}


def _estimate(
    ingredient: BayesianIngredient, weights: _Weights, store: StorePrior | None
) -> ProbabilityResult:
    # 1) crawling corpora (or web recipes), each scaled by its source reliability (§3-3)
    alpha, beta = A0, B0
    observed = False
    for observation in ingredient.observations:
        key = "web_search" if ingredient.source == "web_search" else f"corpus:{observation.corpus}"
        weight = weights.of(key)
        alpha += weight * observation.k_count
        beta += weight * (observation.n_total - observation.k_count)
        observed = observed or observation.n_total > 0

    # 2) curated recipe: α only. Not being listed is no evidence of absence (§3-3)
    if ingredient.curated:
        alpha += weights.of("curated")

    # 3) this store's evidence, already weighted when ⑦ logged it (§3-3, test 22)
    if store is not None:
        alpha += store.alpha - 1.0
        beta += store.beta - 1.0
        prior_source = "store"
    elif observed or ingredient.curated:
        prior_source = "global"  # cluster is deferred (#200)
    else:
        prior_source = "uninformative"

    scale = source_scale(ingredient)
    alpha, beta = alpha * scale, beta * scale
    return ProbabilityResult(
        ingredient=ingredient.ingredient,
        constraint_tags=ingredient.constraint_tags,
        role=ingredient.role,
        posterior_mean=alpha / (alpha + beta),
        alpha_post=alpha,
        beta_post=beta,
        prior_source=prior_source,
        source=ingredient.source,
        depth=ingredient.depth,
        confidence=confidence_score(alpha, beta),
        anomaly_locked=ingredient.anomaly_locked,
        evidence_refs=ingredient.evidence_refs,
    )


def estimate_probabilities(request: BayesianRequest) -> BayesianResponse:
    weights = _Weights(request)
    store = _store_priors(request)
    warnings = list(request.warnings)
    if not request.ingredients:
        warnings.append(
            NodeWarning(
                code="empty_ingredients",
                node=NODE,
                item_id=request.context.item_id,
                message="no ingredients to estimate",
                forces_caution=True,
            )
        )
    return BayesianResponse(
        context=request.context,
        probabilities=[
            _estimate(
                ingredient,
                weights,
                store.get(ingredient.ingredient.ingredient_id)
                if ingredient.ingredient.ingredient_id is not None
                else None,
            )
            for ingredient in request.ingredients
        ],
        warnings=warnings,
        errors=list(request.errors),
    )
