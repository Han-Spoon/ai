"""④ menu query: variant check, recursive expansion, tags, role, observations.

docs/4-tool-DBontology.md §1-2, §1-5, §2-1, §2-2. Pure function over the bundle's
``knowledge`` slice and ``ingredient_graph``. No DB access, no LLM.
"""

from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256

from ai_supervisor.contracts.common import EvidenceRef, IngredientRef, NodeWarning
from ai_supervisor.contracts.external import IngredientGraph, OntologySlice, PersistenceCommand
from ai_supervisor.contracts.ontology import (
    IngredientObservation,
    OntologyIngredient,
    OntologyRequest,
    OntologyResponse,
    OntologyResultMenu,
    SuggestedIngredient,
    VariantSuggestion,
)

from .tags import MAX_DEPTH, NODE

IDENTITY_SHARE = 0.9  # §1-2 role rule 3, revisited in QA (#192)
VARIANT_RELIABILITY = 0.3  # ⓪ §4-3 runtime variant evidence example


def normalize(text: str) -> str:
    """Whitespace-free NFKC lower-case form used for name and alias comparison.

    Must match the DB ``normalize_food_name()``; the shared golden list is still open
    (§8, test 26).
    """

    return "".join(unicodedata.normalize("NFKC", text).split()).lower()


@dataclass
class _Graph:
    names: dict[int, str]
    taxonomy: dict[int, str]
    node_tags: dict[int, set[str]]
    children: dict[int, list[int]]
    edge_tags: dict[tuple[int, int], set[str]]
    lookup: dict[str, int]  # normalized name or alias -> ingredient_id


def _index(graph: IngredientGraph) -> _Graph:
    node_tags: dict[int, set[str]] = defaultdict(set)
    for row in graph.tags:
        node_tags[row.ingredient_id].add(row.tag_code)
    children: dict[int, list[int]] = defaultdict(list)
    for row in graph.compositions:
        children[row.parent_ingredient_id].append(row.child_ingredient_id)
    edge_tags: dict[tuple[int, int], set[str]] = defaultdict(set)
    for row in graph.composition_tags:
        edge_tags[(row.parent_ingredient_id, row.child_ingredient_id)].add(row.tag_code)
    lookup: dict[str, int] = {}
    for row in graph.aliases:
        lookup.setdefault(normalize(row.alias_normalized), row.ingredient_id)
    for row in graph.ingredients:
        lookup[normalize(row.name_normalized)] = row.ingredient_id  # names win over aliases
    return _Graph(
        names={row.ingredient_id: row.canonical_name for row in graph.ingredients},
        taxonomy={row.ingredient_id: row.taxonomy_category for row in graph.ingredients},
        node_tags=node_tags,
        children=children,
        edge_tags=edge_tags,
        lookup=lookup,
    )


@dataclass
class _Node:
    ingredient_id: int
    depth: int
    parent_id: int | None
    path: tuple[int, ...]
    source: str
    tags: set[str]
    observations: list[IngredientObservation]
    basis: str
    evidence_refs: list[str]


@dataclass
class _Warnings:
    item_id: str | None
    items: list[NodeWarning] = field(default_factory=list)

    def add(self, code: str, message: str) -> None:
        if all(w.code != code for w in self.items):
            self.items.append(
                NodeWarning(
                    code=code, node=NODE, item_id=self.item_id, message=message, forces_caution=True
                )
            )


def _map_tokens(tokens: list[str], graph: _Graph) -> tuple[list[int], list[str]]:
    """Map remain/residual tokens to ingredients: exact name/alias, else longest
    names contained in the token. Tokens with no hit go to ``unmapped``."""

    keys = sorted(graph.lookup, key=len, reverse=True)
    found: list[int] = []
    unmapped: list[str] = []
    for token in tokens:
        text = normalize(token)
        if not text:
            continue
        if text in graph.lookup:
            hits = [graph.lookup[text]]
        else:
            hits, rest = [], text
            for key in keys:
                if key and key in rest:
                    hits.append(graph.lookup[key])
                    rest = rest.replace(key, " ")
        if not hits:
            unmapped.append(token)
        for hit in hits:
            if hit not in found:
                found.append(hit)
    return found, unmapped


def _seed_observations(
    ingredient_id: int, corpora: dict[str, int], priors: dict[tuple[str, int], int]
) -> tuple[list[IngredientObservation], str]:
    # depth 0: a missing prior row means "0 of n" in that corpus (§1-5)
    observations = [
        IngredientObservation(
            corpus=corpus, k_count=min(priors.get((corpus, ingredient_id), 0), n), n_total=n
        )
        for corpus, n in corpora.items()
    ]
    return observations, ("direct" if observations else "none")


def _child_observations(
    child_id: int,
    parent: _Node,
    corpora: dict[str, int],
    priors: dict[tuple[str, int], int],
) -> tuple[list[IngredientObservation], str]:
    # depth >= 1: never fill k=0, inherit max(direct, parent) per corpus (§1-5)
    parent_k = {o.corpus: o.k_count for o in parent.observations}
    observations: list[IngredientObservation] = []
    direct_wins = False
    for corpus, n in corpora.items():
        direct = min(priors.get((corpus, child_id), 0), n)
        inherited = parent_k.get(corpus, 0)
        direct_wins = direct_wins or direct > inherited
        observations.append(
            IngredientObservation(corpus=corpus, k_count=max(direct, inherited), n_total=n)
        )
    if not parent.observations and not direct_wins:
        return [], "none"
    return observations, ("direct" if direct_wins else "inherited")


def _expand(
    seeds: list[tuple[int, str, list[str]]],
    graph: _Graph,
    corpora: dict[str, int],
    priors: dict[tuple[str, int], int],
    warnings: _Warnings,
) -> list[_Node]:
    """Every occurrence of every ingredient along part-of paths, seeds first."""

    nodes: list[_Node] = []

    def walk(parent: _Node) -> None:
        for child in graph.children.get(parent.ingredient_id, ()):
            if child in parent.path:
                warnings.add("cycle_detected", f"composition cycle at {parent.ingredient_id} -> {child}")
                continue
            if parent.depth + 1 > MAX_DEPTH:
                warnings.add(
                    "max_depth_reached",
                    f"sub-ingredients deeper than {MAX_DEPTH} were not expanded",
                )
                return
            observations, basis = _child_observations(child, parent, corpora, priors)
            node = _Node(
                ingredient_id=child,
                depth=parent.depth + 1,
                parent_id=parent.ingredient_id,
                path=parent.path + (child,),
                source="expanded",
                tags=graph.node_tags.get(child, set()) | graph.edge_tags.get(
                    (parent.ingredient_id, child), set()
                ),
                observations=observations,
                basis=basis,
                evidence_refs=list(parent.evidence_refs),
            )
            nodes.append(node)
            walk(node)

    for ingredient_id, source, evidence_refs in seeds:
        if source == "variant_suggested":
            observations, basis = [], "none"  # base recipes never observed the variant (§1-5)
        else:
            observations, basis = _seed_observations(ingredient_id, corpora, priors)
        node = _Node(
            ingredient_id=ingredient_id,
            depth=0,
            parent_id=None,
            path=(ingredient_id,),
            source=source,
            tags=set(graph.node_tags.get(ingredient_id, set())),
            observations=observations,
            basis=basis,
            evidence_refs=list(evidence_refs),
        )
        nodes.append(node)
        walk(node)
    return nodes


def _drop_confirmed(nodes: list[_Node], confirmed: dict[int, str]) -> list[_Node]:
    """§2-1 table: drop confirmed ingredients; drop paths through an ``absent`` one,
    keep what sits under a ``present`` one (nobody confirmed those)."""

    absent = {i for i, status in confirmed.items() if status == "absent"}
    return [
        n
        for n in nodes
        if n.ingredient_id not in confirmed and not absent.intersection(n.path[:-1])
    ]


def _merge(nodes: list[_Node]) -> list[_Node]:
    """One entry per ingredient: shallowest occurrence wins, tags are unioned so a
    path-only tag from another parent is not lost (FN)."""

    ordered = sorted(enumerate(nodes), key=lambda pair: (pair[1].depth, pair[0]))
    merged: dict[int, _Node] = {}
    for _, node in ordered:
        if node.ingredient_id in merged:
            merged[node.ingredient_id].tags |= node.tags
        else:
            merged[node.ingredient_id] = _Node(**{**node.__dict__, "tags": set(node.tags)})
    return list(merged.values())


def _role(node: _Node, graph: _Graph, menu_name: str) -> str:
    """§1-2 role rules. No LLM; the same menu always gets the same role."""

    if node.depth >= 1:
        return "hidden"
    if node.source == "variant_suggested":
        return "identity"
    names = [normalize(graph.names.get(node.ingredient_id, ""))]
    names += [key for key, value in graph.lookup.items() if value == node.ingredient_id]
    if any(name and name in menu_name for name in names):
        return "identity"
    if any(o.n_total and o.k_count / o.n_total >= IDENTITY_SHARE for o in node.observations):
        return "identity"
    return "component"


def _ref(ingredient_id: int, graph: _Graph) -> IngredientRef:
    return IngredientRef(
        ingredient_id=ingredient_id,
        canonical_name=graph.names.get(ingredient_id, f"ingredient:{ingredient_id}"),
    )


def _to_wire(
    node: _Node, graph: _Graph, menu_name: str, curated: set[int], locked: set[int]
) -> OntologyIngredient:
    return OntologyIngredient(
        ingredient=_ref(node.ingredient_id, graph),
        taxonomy_category=graph.taxonomy.get(node.ingredient_id, "unknown"),
        constraint_tags=sorted(node.tags),
        role=_role(node, graph, menu_name),
        depth=node.depth,
        parent_ingredient=_ref(node.parent_id, graph) if node.parent_id is not None else None,
        source=node.source,
        curated=node.ingredient_id in curated,
        observation_basis=node.basis,
        observations=node.observations,
        anomaly_locked=node.ingredient_id in locked,
        evidence_refs=node.evidence_refs,
    )


def _cache_command(
    request: OntologyRequest, menu_id: int, revision: int, snapshot: list[OntologyIngredient]
) -> PersistenceCommand:
    store_id = request.context.store_id
    key = json.dumps(
        {"store_id": store_id, "menu_id": menu_id, "menu_revision": revision},
        sort_keys=True,
        separators=(",", ":"),
    )
    action = "save_menu_ingredient_cache"
    return PersistenceCommand(
        command_id=f"cache:{request.context.item_id}",
        operation_id=None,
        item_id=request.context.item_id,
        card_id=None,
        action=action,
        idempotency_key="sha256:" + sha256(f"{action}|{key}".encode()).hexdigest(),
        payload={
            "menu_id": menu_id,
            "menu_revision": revision,
            # full list before confirmed ingredients are dropped, without per-request lock
            "ingredients_snapshot": [
                item.model_dump(mode="json", exclude={"anomaly_locked"}) for item in snapshot
            ],
        },
    )


def _empty_menu() -> OntologyResultMenu:
    return OntologyResultMenu(
        menu_id=None,
        base_menu_id=None,
        remain_token=None,
        menu_category_id=None,
        menu_category=None,
        exists_in_db=False,
        is_variant=False,
        variant_origin=None,
    )


def _remain_token(request: OntologyRequest) -> str | None:
    """Name left after removing the chosen candidate's name (차돌된장찌개 - 된장찌개)."""

    selected = next(
        (c for c in request.menu.match_candidates if c.menu_id == request.menu.menu_id), None
    )
    if selected is None:
        return None
    name = normalize(request.menu.normalized_menu_name)
    base = normalize(selected.canonical_name)
    if not base or base == name or base not in name:
        return None
    return name.replace(base, "", 1) or None


def query_ontology(request: OntologyRequest, *, now: datetime | None = None) -> OntologyResponse:
    knowledge: OntologySlice = request.knowledge
    menu_row = knowledge.menu
    item_id = request.context.item_id
    warnings = _Warnings(item_id)

    # Unknown menu, or the no-candidate fallback path: the fallback slice carries no base
    # menu name to cut the remain token from, so go to web search (§5-2).
    if request.menu.menu_id is None or menu_row is None:
        return OntologyResponse(
            context=request.context,
            menu=_empty_menu(),
            menu_ambiguity_flags=[],
            ingredients=[],
            evidence=[],
            variant_suggestion=None,
            unmapped_tokens=[],
            cache_command=None,
            warnings=list(request.warnings),
            errors=list(request.errors),
        )
    if menu_row.menu_id != request.menu.menu_id:
        raise ValueError("knowledge.menu does not belong to the selected menu_id")

    graph = _index(request.ingredient_graph)
    menu_name = normalize(request.menu.normalized_menu_name)
    corpora = {row.corpus: row.n_total for row in knowledge.menu_recipe_corpora}
    priors = {(row.corpus, row.ingredient_id): row.k_count for row in knowledge.menu_ingredient_priors}
    curated = [row.ingredient_id for row in knowledge.recipe_ingredients]

    # §2-1 step 3: seeds = curated recipe ∪ corpus ingredients
    seed_ids = list(dict.fromkeys(curated))
    seed_ids += sorted({i for _, i in priors} - set(seed_ids))
    if not seed_ids:
        warnings.add("empty_recipe", f"menu {menu_row.menu_id} has no recipe ingredients")

    # §2-2: DB columns first; parse only when the menu row is not a registered variant
    origin: str | None = None
    base_menu_id = remain = None
    if menu_row.base_menu_id is not None:
        if menu_row.remain_token and menu_row.base_menu_id != menu_row.menu_id:
            origin, base_menu_id, remain = "db_registered", menu_row.base_menu_id, menu_row.remain_token
        else:
            warnings.add("broken_base_menu", f"menu {menu_row.menu_id} has an unusable base_menu_id")
        remain_tokens = list(request.menu.residual_tokens)
    else:
        parsed = _remain_token(request)
        remain_tokens = ([parsed] if parsed else []) + list(request.menu.residual_tokens)

    suggested_ids, unmapped = _map_tokens(remain_tokens, graph)
    suggested_ids = [i for i in suggested_ids if i not in seed_ids]
    if suggested_ids and origin is None:
        origin = "runtime_tagged"
        base_menu_id = menu_row.menu_id
        remain = " ".join(remain_tokens)

    evidence: list[EvidenceRef] = []
    variant_refs: list[str] = []
    if suggested_ids:
        variant_evidence = EvidenceRef(
            evidence_id=f"runtime:{item_id}:ocr_variant_tag:0",
            evidence_class="soft",
            source_type="ocr_variant_tag",
            source_ref=f"{item_id}:{' '.join(remain_tokens)}",
            verification_status="pending_review",
            reliability_weight=VARIANT_RELIABILITY,
            observed_at=now or datetime.now(timezone.utc),
        )
        evidence.append(variant_evidence)
        variant_refs = [variant_evidence.evidence_id]

    for ingredient_id in [*seed_ids, *suggested_ids]:
        if ingredient_id not in graph.names:
            warnings.add(
                "constraint_tags_lookup_failed",
                f"ingredient {ingredient_id} is not in ingredient_graph",
            )

    seeds = [(i, "recipe", []) for i in seed_ids]
    seeds += [(i, "variant_suggested", variant_refs) for i in suggested_ids]
    occurrences = _expand(seeds, graph, corpora, priors, warnings)

    confirmed = {
        c.ingredient.ingredient_id: c.status
        for c in request.confirmed_ingredients
        if c.ingredient.ingredient_id is not None
    }
    locked = {
        ref.ingredient_id for ref in request.anomaly_locked_ingredients if ref.ingredient_id is not None
    }
    curated_set = set(curated)
    full = [_to_wire(n, graph, menu_name, curated_set, locked) for n in _merge(occurrences)]
    kept = (
        _drop_confirmed(occurrences, confirmed) if request.unconfirmed_only else occurrences
    )
    ingredients = [_to_wire(n, graph, menu_name, curated_set, locked) for n in _merge(kept)]

    runtime = origin == "runtime_tagged"
    menu = OntologyResultMenu(
        menu_id=None if runtime else menu_row.menu_id,
        base_menu_id=base_menu_id,
        remain_token=remain,
        menu_category_id=menu_row.menu_category_id,
        menu_category=None,  # the slice carries only the category id
        exists_in_db=not runtime,
        is_variant=origin is not None,
        variant_origin=origin,
    )
    suggestion = (
        VariantSuggestion(
            base_menu_id=menu_row.menu_id,
            remain_token=remain,
            suggested_ingredients=[
                SuggestedIngredient(
                    ingredient=item.ingredient, constraint_tags=item.constraint_tags, role=item.role
                )
                for item in full
                if item.source == "variant_suggested"
            ],
            evidence=evidence[0],
        )
        if runtime
        else None
    )

    # §2-1 step 6. Not for a storeless scan, a still-valid cache, or a runtime variant
    # (its suggestions are unreviewed and must not enter the base menu's cache).
    cache = knowledge.cache
    cache_valid = cache is not None and cache.menu_revision == menu_row.knowledge_revision
    cache_command = (
        _cache_command(request, menu_row.menu_id, menu_row.knowledge_revision, full)
        if request.context.store_id is not None and not cache_valid and not runtime
        else None
    )

    return OntologyResponse(
        context=request.context,
        menu=menu,
        menu_ambiguity_flags=list(menu_row.ambiguity_flags),
        ingredients=ingredients,
        evidence=evidence,
        variant_suggestion=suggestion,
        unmapped_tokens=unmapped,
        cache_command=cache_command,
        warnings=[*request.warnings, *warnings.items],
        errors=list(request.errors),
    )
