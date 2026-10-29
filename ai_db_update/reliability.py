"""⑦ §3-1 source reliability: compare each source's claim with the owner's answer.

reliability(source) = (agree + 1) / (compared + 2) is kept by the backend. ⑦ only
returns the per-source increments as ``update_source_reliability`` payloads.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_supervisor.contracts.bayesian import BayesianIngredient
from ai_supervisor.contracts.db_update import UpdateSourceReliabilityPayload

@dataclass(frozen=True)
class SourceClaim:
    source_key: str  # corpus:{site} / curated / web_search, same keys as ⑤ weights
    present: bool


def claims_for(ingredient: BayesianIngredient) -> list[SourceClaim]:
    """What each source said about this ingredient (§3-1 "무엇을 비교하나").

    - corpus site: "present" if (k+1)/(n+2) ≥ 0.5, else "absent"
    - curated recipe, web recipe: listed means "present"; not listed is no claim
    """

    claims: list[SourceClaim] = []
    if ingredient.source == "web_search":
        if ingredient.observations:
            claims.append(SourceClaim("web_search", True))
    else:
        for o in ingredient.observations:
            if o.n_total > 0:
                claims.append(SourceClaim(f"corpus:{o.corpus}", (o.k_count + 1) / (o.n_total + 2) >= 0.5))
    if ingredient.curated:
        claims.append(SourceClaim("curated", True))
    return claims


def reliability_updates(
    claims: list[SourceClaim],
    *,
    present: bool,
    store_id: int,
    menu_id: int,
    ingredient_id: int,
) -> list[UpdateSourceReliabilityPayload]:
    """One increment per source for this owner-confirmed ingredient.

    Every owner answer is ground truth (no anomaly lock, 10/27 meeting). The trigger is
    the store·menu·ingredient, so the same source is counted once for it (the backend
    dedupes by the content key) even if the owner answers again later.
    """

    trigger = f"owner_confirmation:{store_id}:{menu_id}:{ingredient_id}"
    seen: dict[str, SourceClaim] = {}
    for claim in claims:
        seen.setdefault(claim.source_key, claim)
    return [
        UpdateSourceReliabilityPayload(
            source_type=key,
            correct_count_delta=int(claim.present == present),
            comparison_count_delta=1,
            trigger_evidence_ref=trigger,
        )
        for key, claim in sorted(seen.items())
    ]
