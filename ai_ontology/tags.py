"""④ tag lookup: risk tags for confirmed ingredients (docs/4-tool-DBontology.md §2-7).

Pure function over the bundle's ``ingredient_graph``. No DB access, no LLM.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ai_supervisor.contracts.common import NodeWarning
from ai_supervisor.contracts.external import IngredientGraph

NODE = "ontology"
MAX_DEPTH = 5  # same limit as the menu expansion (§2-1)


@dataclass
class TagLookupOutcome:
    tags: dict[int, list[str]]
    warnings: list[NodeWarning] = field(default_factory=list)


@dataclass
class _GraphIndex:
    known: set[int]
    node_tags: dict[int, set[str]]
    children: dict[int, list[int]]
    edge_tags: dict[tuple[int, int], set[str]]


def _index(graph: IngredientGraph) -> _GraphIndex:
    node_tags: dict[int, set[str]] = defaultdict(set)
    for row in graph.tags:
        node_tags[row.ingredient_id].add(row.tag_code)
    children: dict[int, list[int]] = defaultdict(list)
    for row in graph.compositions:
        children[row.parent_ingredient_id].append(row.child_ingredient_id)
    edge_tags: dict[tuple[int, int], set[str]] = defaultdict(set)
    for row in graph.composition_tags:
        edge_tags[(row.parent_ingredient_id, row.child_ingredient_id)].add(row.tag_code)
    return _GraphIndex(
        known={row.ingredient_id for row in graph.ingredients},
        node_tags=node_tags,
        children=children,
        edge_tags=edge_tags,
    )


def _warning(code: str, item_id: str | None, message: str) -> NodeWarning:
    return NodeWarning(
        code=code, node=NODE, item_id=item_id, message=message, forces_caution=True
    )


def lookup_tags(
    ingredient_ids: list[int], graph: IngredientGraph, item_id: str | None
) -> TagLookupOutcome:
    """Return each ingredient's tags, including what its sub-ingredients carry.

    A confirmed "present" ingredient also contains its part-of children (김치 → 멸치액젓
    → 멸치), so their node and path tags are added too. Leaving them out would hide
    ``is_fish`` from a 김치 confirmation (FN).

    Lookup failures never shrink to a silent ``[]``: an unknown ingredient or an
    unusable graph adds ``constraint_tags_lookup_failed`` (forces_caution).
    """

    outcome = TagLookupOutcome(tags={})
    if graph.status != "ok":
        outcome.warnings.append(
            _warning(
                "constraint_tags_lookup_failed",
                item_id,
                f"ingredient_graph is {graph.status}; confirmed ingredient tags are unknown",
            )
        )
        for ingredient_id in ingredient_ids:
            outcome.tags[ingredient_id] = []
        return outcome

    index = _index(graph)
    codes_seen: set[str] = set()

    def add_warning(code: str, message: str) -> None:
        if code not in codes_seen:
            codes_seen.add(code)
            outcome.warnings.append(_warning(code, item_id, message))

    for ingredient_id in ingredient_ids:
        if ingredient_id not in index.known:
            outcome.tags[ingredient_id] = []
            add_warning(
                "constraint_tags_lookup_failed",
                f"ingredient {ingredient_id} is not in ingredient_graph",
            )
            continue

        collected: set[str] = set(index.node_tags.get(ingredient_id, ()))

        def walk(parent: int, path: tuple[int, ...]) -> None:
            if len(path) > MAX_DEPTH:
                if not index.children.get(parent):
                    return
                add_warning(
                    "max_depth_reached",
                    f"sub-ingredients of {ingredient_id} deeper than {MAX_DEPTH} were not read",
                )
                return
            for child in index.children.get(parent, ()):
                if child in path:
                    add_warning(
                        "cycle_detected",
                        f"composition cycle at {parent} -> {child}",
                    )
                    continue
                collected.update(index.node_tags.get(child, ()))
                collected.update(index.edge_tags.get((parent, child), ()))
                walk(child, path + (child,))

        walk(ingredient_id, (ingredient_id,))
        outcome.tags[ingredient_id] = sorted(collected)

    return outcome
