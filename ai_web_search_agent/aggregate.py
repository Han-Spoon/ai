"""⑥ stage 2: count mapped web recipes per standard ingredient and site (docs/6-agent-websearch.md §2-2, §2-6).

No search, no LLM, no DB. ④ has already mapped every recipe's ingredients, so names
like 간장/진간장 are counted once. ``review_notes`` reasons come from fixed rules; the
LLM that rewrites them into friendlier sentences is added later (§2-6).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ai_supervisor.contracts.bayesian import BayesianIngredient
from ai_supervisor.contracts.common import IngredientRef, NodeWarning
from ai_supervisor.contracts.ontology import IngredientObservation
from ai_supervisor.contracts.web_search import (
    MappedIngredient,
    MappedSource,
    ReviewNote,
    WebAggregateRequest,
    WebAggregateResponse,
)

MIN_SOURCES = 10
PRIORITY_DOMAINS = ("10000recipe.com", "wtable.co.kr", "semie.cooking")
RARE_MAX_RECIPES = 2
COMMON_MIN_INGREDIENTS = 3

IngredientKey = tuple[str, int | str]


@dataclass(frozen=True)
class AggregateOutcome:
    found: bool
    ingredients: list[BayesianIngredient]
    review_notes: list[ReviewNote]
    warnings: list[NodeWarning]


def _key(ingredient: IngredientRef) -> IngredientKey:
    if ingredient.ingredient_id is not None:
        return ("id", ingredient.ingredient_id)
    return ("name", ingredient.canonical_name)


def _compact(text: str) -> str:
    return "".join(text.split()).lower()


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_priority_source(url: str) -> bool:
    domain = _domain(url)
    return any(domain == d or domain.endswith("." + d) for d in PRIORITY_DOMAINS)


def _unique_ingredients(source: MappedSource) -> dict[IngredientKey, MappedIngredient]:
    unique: dict[IngredientKey, MappedIngredient] = {}
    for item in source.ingredients:
        unique.setdefault(_key(item.ingredient), item)
    return unique


def is_valid_source(source: MappedSource) -> bool:
    """A recipe with no extracted ingredient is not counted at all (§3-2)."""

    return bool(source.ingredients)


def count_ingredients(sources: list[MappedSource]) -> list[BayesianIngredient]:
    """Per standard ingredient, count recipes per site (``observations``, §2-2).

    Every site that contributed a valid recipe gets an observation, including
    ``k_count: 0``. Unmapped ingredients are kept with ``ingredient_id: null``.
    """

    valid = [s for s in sources if is_valid_source(s)]
    n_total: dict[str, int] = {}
    for source in valid:
        n_total[source.corpus] = n_total.get(source.corpus, 0) + 1
    corpora = sorted(n_total)

    first_seen: dict[IngredientKey, MappedIngredient] = {}
    roles: dict[IngredientKey, str] = {}
    tags: dict[IngredientKey, set[str]] = {}
    k_count: dict[IngredientKey, dict[str, int]] = {}
    refs: dict[IngredientKey, list[str]] = {}
    for source in valid:
        for key, item in _unique_ingredients(source).items():
            first_seen.setdefault(key, item)
            if item.role is not None:
                roles.setdefault(key, item.role)
            tags.setdefault(key, set()).update(item.constraint_tags)
            per_site = k_count.setdefault(key, {})
            per_site[source.corpus] = per_site.get(source.corpus, 0) + 1
            if source.evidence_id not in refs.setdefault(key, []):
                refs[key].append(source.evidence_id)

    def total(key: IngredientKey) -> int:
        return sum(k_count[key].values())

    ordered = sorted(first_seen, key=lambda k: (-total(k), first_seen[k].ingredient.canonical_name))
    return [
        BayesianIngredient(
            ingredient=first_seen[key].ingredient,
            constraint_tags=sorted(tags[key]),
            role=roles.get(key),
            source="web_search",
            depth=0,
            curated=False,
            observation_basis="direct",
            observations=[
                IngredientObservation(
                    corpus=corpus, k_count=k_count[key].get(corpus, 0), n_total=n_total[corpus]
                )
                for corpus in corpora
            ],
            anomaly_locked=False,
            evidence_refs=refs[key],
        )
        for key in ordered
    ]


def review_notes(menu_name: str, new_sources: list[MappedSource]) -> list[ReviewNote]:
    """Rule-based admin review reasons for this batch of new recipes (§2-6).

    Cached recipes were reviewed before, so only ``source_origin: new`` counts.
    Order is A (same dish?) → B (ingredients extracted right?) → C (risky ingredients).
    Rule 5 (``llm_only_ingredient``) needs the LLM extractor and is not produced yet.
    """

    recipes = [s for s in new_sources if is_valid_source(s)]
    if not recipes:
        return []
    count = len(recipes)
    notes: list[ReviewNote] = []

    general = sorted({_domain(s.source_url) for s in recipes if not is_priority_source(s.source_url)})
    if general:
        notes.append(ReviewNote(
            rule_code="general_web_source",
            message=f"우선 레시피 사이트가 아닌 출처가 섞여 있어요: {', '.join(general)}",
        ))

    menu = _compact(menu_name)
    mismatched = [s.title for s in recipes if menu not in _compact(s.title)]
    if mismatched:
        notes.append(ReviewNote(
            rule_code="title_mismatch",
            message=f"제목에 '{menu_name}'이(가) 없는 레시피가 {len(mismatched)}개 있어요: {', '.join(mismatched)}",
        ))

    appearances: dict[IngredientKey, int] = {}
    items: dict[IngredientKey, MappedIngredient] = {}
    for source in recipes:
        for key, item in _unique_ingredients(source).items():
            appearances[key] = appearances.get(key, 0) + 1
            items.setdefault(key, item)

    def name(key: IngredientKey) -> str:
        return items[key].ingredient.canonical_name

    common = [k for k, n in appearances.items() if n * 2 >= count]
    if len(common) < COMMON_MIN_INGREDIENTS:
        notes.append(ReviewNote(
            rule_code="inconsistent_recipes",
            message=f"레시피 절반 이상에 나오는 재료가 {len(common)}개뿐이라 서로 다른 음식이 섞였을 수 있어요",
        ))

    rare = sorted((k for k, n in appearances.items() if n <= RARE_MAX_RECIPES), key=name)
    if rare:
        notes.append(ReviewNote(
            rule_code="rare_ingredient",
            message=f"레시피 {count}개 중 1~2개에만 나온 재료예요: "
            + ", ".join(f"{name(k)} {appearances[k]}/{count}" for k in rare),
        ))

    unmapped = sorted((k for k in appearances if items[k].ingredient.ingredient_id is None), key=name)
    if unmapped:
        notes.append(ReviewNote(
            rule_code="unmapped_ingredient",
            message="표준 재료로 맞추지 못해 위험 태그를 알 수 없는 재료예요: "
            + ", ".join(name(k) for k in unmapped),
        ))

    risky = sorted(
        (k for k, n in appearances.items() if items[k].constraint_tags and n * 2 < count),
        key=name,
    )
    if risky:
        notes.append(ReviewNote(
            rule_code="risky_ingredient_partial",
            message="위험 태그가 있는데 레시피 절반 미만에만 나온 재료예요: "
            + ", ".join(f"{name(k)} {appearances[k]}/{count}" for k in risky),
        ))
    return notes


def aggregate_sources(request: WebAggregateRequest) -> AggregateOutcome:
    """Core of ⑥ stage 2. Input warnings are kept by the caller; only new ones are returned."""

    ingredients = count_ingredients(request.mapped_sources)
    valid_count = sum(1 for s in request.mapped_sources if is_valid_source(s))
    warnings: list[NodeWarning] = []
    if ingredients and valid_count < MIN_SOURCES:
        warnings.append(NodeWarning(
            code="insufficient_sources",
            node="web_search",
            item_id=request.context.item_id,
            message=f"유효 레시피가 {valid_count}개로 기준 {MIN_SOURCES}개보다 적어요",
            forces_caution=True,
        ))
    new_sources = [s for s in request.mapped_sources if s.source_origin == "new"]
    return AggregateOutcome(
        found=bool(ingredients),
        ingredients=ingredients,
        review_notes=review_notes(request.menu.normalized_menu_name, new_sources),
        warnings=warnings,
    )


class WebAggregateAgent:
    """⑥ stage 2 node, matching the Supervisor ``NodeAdapter`` shape.

    New-source originals are not sent back; the Supervisor already has them from the
    stage 1 response (⓪ §4-5). Input warnings such as ④ ``unmapped_constraint_tags``
    are kept so ⑧ still blocks SAFE.
    """

    async def run(self, request: WebAggregateRequest) -> WebAggregateResponse:
        outcome = aggregate_sources(request)
        return WebAggregateResponse(
            context=request.context,
            menu=request.menu,
            found=outcome.found,
            ingredients=outcome.ingredients,
            review_notes=outcome.review_notes,
            warnings=[*request.warnings, *outcome.warnings],
            errors=list(request.errors),
        )
