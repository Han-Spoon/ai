"""④ map_ingredients: web ingredient names -> standard ingredient + tags (§2-7).

Pure function over the bundle's ``ingredient_graph``. No DB access, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_supervisor.contracts.common import IngredientRef, NodeWarning
from ai_supervisor.contracts.external import IngredientGraph
from crawling.normalize_ingredients import canonicalize, clean_text

from .query import _index, normalize
from .tags import NODE, lookup_tags


@dataclass
class MappedName:
    input_name: str
    ingredient: IngredientRef
    constraint_tags: list[str]


@dataclass
class MappingOutcome:
    results: list[MappedName]
    warnings: list[NodeWarning] = field(default_factory=list)


def _candidates(name: str) -> list[str]:
    """Raw name first, then the crawling cleaner and canonical name, the same rules
    that built the DB seed (``crawling/normalize_ingredients.py``)."""

    cleaned = clean_text(name)
    canonical, _ = canonicalize(cleaned)
    return [normalize(text) for text in (name, cleaned, canonical) if normalize(text)]


def map_ingredients(
    names: list[str], graph: IngredientGraph, item_id: str | None
) -> MappingOutcome:
    """Map each name to an ingredient; unmapped names stay with ``ingredient_id: null``.

    Tags come from ``lookup_tags``, so a mapped composite (김치) also carries what its
    sub-ingredients carry (is_fish). The web path never expands, so without this the
    hidden allergen would be lost (FN).
    """

    index = _index(graph) if graph.status == "ok" else None
    ids: dict[str, int | None] = {}
    for name in names:
        ids[name] = next(
            (index.lookup[key] for key in _candidates(name) if index and key in index.lookup),
            None,
        )

    mapped_ids = list(dict.fromkeys(i for i in ids.values() if i is not None))
    tags = lookup_tags(mapped_ids, graph, item_id)
    outcome = MappingOutcome(results=[], warnings=list(tags.warnings))
    unmapped: list[str] = []
    for name in names:
        ingredient_id = ids[name]
        if ingredient_id is None:
            unmapped.append(name)
            outcome.results.append(
                MappedName(
                    input_name=name,
                    ingredient=IngredientRef(ingredient_id=None, canonical_name=name),
                    constraint_tags=[],
                )
            )
            continue
        outcome.results.append(
            MappedName(
                input_name=name,
                ingredient=IngredientRef(
                    ingredient_id=ingredient_id, canonical_name=index.names[ingredient_id]
                ),
                constraint_tags=tags.tags[ingredient_id],
            )
        )
    if unmapped:
        outcome.warnings.append(
            NodeWarning(
                code="unmapped_constraint_tags",
                node=NODE,
                item_id=item_id,
                message=f"{len(unmapped)} web ingredient names did not map: {', '.join(unmapped[:5])}",
                forces_caution=True,
            )
        )
    return outcome
