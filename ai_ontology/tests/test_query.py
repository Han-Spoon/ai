import asyncio
from datetime import datetime, timezone

from ai_ontology import OntologyTool, query_ontology
from ai_supervisor.contracts.common import NodeContext, NodeWarning
from ai_supervisor.contracts.external import IngredientGraph, OntologySlice
from ai_supervisor.contracts.ontology import OntologyRequest

ITEM_ID = "scan-123:0"
NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)

# 1 김치 → 2 액젓 → 3 새우 (김치→액젓 path carries is_shellfish)
INGREDIENTS = {
    1: ("김치", "fermented"),
    2: ("액젓", "fermented"),
    3: ("새우", "base"),
    4: ("돼지고기", "base"),
    5: ("두부", "processed"),
    6: ("소고기", "base"),
    7: ("마늘", "base"),
    8: ("밥", "base"),
    9: ("참치", "base"),
}
TAGS = ((2, "is_fish"), (3, "is_shellfish"), (4, "is_pork"), (5, "is_soybean"), (6, "is_beef"))


def graph(*, compositions=((1, 2), (2, 3)), composition_tags=((1, 2, "is_shellfish"),), drop=()):
    return IngredientGraph(
        status="ok",
        ingredients=[
            {
                "ingredient_id": i,
                "canonical_name": name,
                "name_normalized": name,
                "localized_name": None,
                "taxonomy_category": taxonomy,
            }
            for i, (name, taxonomy) in INGREDIENTS.items()
            if i not in drop
        ],
        aliases=[{"ingredient_id": 6, "alias_normalized": "차돌"}],
        tags=[{"ingredient_id": i, "tag_code": t} for i, t in TAGS],
        compositions=[
            {"parent_ingredient_id": p, "child_ingredient_id": c, "ambiguity_flag": None}
            for p, c in compositions
        ],
        composition_tags=[
            {"parent_ingredient_id": p, "child_ingredient_id": c, "tag_code": t}
            for p, c, t in composition_tags
        ],
    )


def knowledge(
    *,
    menu_id=1001,
    base_menu_id=None,
    remain_token=None,
    recipe=(1, 4, 5),
    corpora=(("10000recipe", 10), ("semie", 5)),
    priors=(("10000recipe", 1, 10), ("10000recipe", 4, 8), ("10000recipe", 7, 3)),
    cache_revision=None,
    flags=("has_unclear_jeotgal",),
):
    return OntologySlice(
        status="ok",
        menu={
            "menu_id": menu_id,
            "base_menu_id": base_menu_id,
            "remain_token": remain_token,
            "menu_category_id": 11,
            "source": "curated",
            "ambiguity_flags": list(flags),
            "knowledge_revision": 7,
        },
        recipe_ingredients=[{"ingredient_id": i} for i in recipe],
        menu_recipe_corpora=[{"corpus": c, "n_total": n} for c, n in corpora],
        menu_ingredient_priors=[{"corpus": c, "ingredient_id": i, "k_count": k} for c, i, k in priors],
        cache=(
            {"menu_revision": cache_revision, "ingredients_snapshot": []}
            if cache_revision is not None
            else None
        ),
    )


def request(
    *,
    name="김치찌개",
    menu_id=1001,
    candidate_name="김치찌개",
    residual=(),
    k=None,
    g=None,
    store_id=123456,
    confirmed=(),
    locked=(),
    warnings=(),
):
    return OntologyRequest(
        context=NodeContext(
            schema_version="3.0",
            trace_id="0199a2f0-0000-7000-8000-000000000001",
            scan_session_id="scan-123",
            store_id=store_id,
            call_scope="item",
            item_id=ITEM_ID,
        ),
        menu={
            "menu_id": menu_id,
            "normalized_menu_name": name,
            "match_candidates": (
                [
                    {
                        "menu_id": menu_id,
                        "menu_category_id": 11,
                        "canonical_name": candidate_name,
                        "score": 0.9,
                        "match_type": "canonical",
                    }
                ]
                if menu_id is not None
                else []
            ),
            "residual_tokens": list(residual),
        },
        unconfirmed_only=True,
        confirmed_ingredients=[
            {"ingredient": {"ingredient_id": i, "canonical_name": INGREDIENTS[i][0]}, "status": s}
            for i, s in confirmed
        ],
        anomaly_locked_ingredients=[
            {"ingredient_id": i, "canonical_name": INGREDIENTS[i][0]} for i in locked
        ],
        knowledge=k or knowledge(),
        ingredient_graph=g or graph(),
        warnings=list(warnings),
        errors=[],
    )


def by_name(response):
    return {item.ingredient.canonical_name: item for item in response.ingredients}


def codes(response):
    return [w.code for w in response.warnings]


def test_expands_kimchi_to_shrimp_with_depth_and_taxonomy():  # §7-1, 19
    items = by_name(query_ontology(request(), now=NOW))
    assert (items["김치"].depth, items["액젓"].depth, items["새우"].depth) == (0, 1, 2)
    assert items["액젓"].parent_ingredient.canonical_name == "김치"
    assert items["새우"].constraint_tags == ["is_shellfish"]
    assert items["액젓"].constraint_tags == ["is_fish", "is_shellfish"]  # node + path tag
    assert items["김치"].taxonomy_category == "fermented"
    assert items["액젓"].source == "expanded"


def test_db_registered_variant_reads_columns_only():  # §7-2
    k = knowledge(menu_id=1002, base_menu_id=1000, remain_token="차돌")
    res = query_ontology(
        request(name="차돌된장찌개", menu_id=1002, candidate_name="차돌된장찌개", k=k), now=NOW
    )
    assert res.menu.variant_origin == "db_registered"
    assert (res.menu.menu_id, res.menu.base_menu_id, res.menu.remain_token) == (1002, 1000, "차돌")
    assert res.variant_suggestion is None
    assert {i.source for i in res.ingredients} <= {"recipe", "expanded"}


def test_runtime_variant_is_suggested_not_saved():  # §7-3, 14
    k = knowledge(menu_id=1000, recipe=(5, 7), priors=())
    res = query_ontology(
        request(name="차돌된장찌개", menu_id=1000, candidate_name="된장찌개", k=k), now=NOW
    )
    assert res.menu.variant_origin == "runtime_tagged"
    assert (res.menu.menu_id, res.menu.base_menu_id, res.menu.exists_in_db) == (None, 1000, False)
    beef = by_name(res)["소고기"]
    assert beef.source == "variant_suggested"
    assert beef.observations == [] and beef.observation_basis == "none"
    assert beef.role == "identity"
    assert res.variant_suggestion.suggested_ingredients[0].ingredient.canonical_name == "소고기"
    assert res.variant_suggestion.remain_token == "차돌"
    assert beef.evidence_refs == [res.variant_suggestion.evidence.evidence_id]
    assert res.cache_command is None  # unreviewed suggestion stays out of the base cache


def test_unmapped_remain_is_base_menu_with_unmapped_token():  # §7-9
    k = knowledge(menu_id=1000, recipe=(5,), priors=())
    res = query_ontology(
        request(name="우리집된장찌개", menu_id=1000, candidate_name="된장찌개", k=k), now=NOW
    )
    assert res.menu.variant_origin is None and res.menu.exists_in_db
    assert res.unmapped_tokens == ["우리집"]


def test_residual_option_token_becomes_variant_ingredient():
    res = query_ontology(request(residual=["돼지고기", "소고기"]), now=NOW)
    beef = by_name(res)["소고기"]
    assert beef.source == "variant_suggested"
    assert by_name(res)["돼지고기"].source == "recipe"  # already in the recipe, not re-suggested


def test_unknown_menu_returns_not_in_db():  # §7-4
    res = query_ontology(request(menu_id=None), now=NOW)
    assert res.menu.exists_in_db is False and res.menu.base_menu_id is None
    assert res.ingredients == [] and res.cache_command is None


def test_storeless_scan_expands_without_cache():  # §7-5-b
    res = query_ontology(request(store_id=None), now=NOW)
    assert "새우" in by_name(res)
    assert res.cache_command is None


def test_cycle_is_cut():  # §7-6
    res = query_ontology(request(g=graph(compositions=((1, 2), (2, 1)))), now=NOW)
    assert "cycle_detected" in codes(res)


def test_confirmed_ingredients_are_dropped():  # §7-7
    res = query_ontology(request(confirmed=[(4, "present"), (5, "absent")]), now=NOW)
    assert {"돼지고기", "두부"}.isdisjoint(by_name(res))


def test_present_composite_keeps_its_sub_ingredients():  # §7-7-b
    items = by_name(query_ontology(request(confirmed=[(1, "present")]), now=NOW))
    assert "김치" not in items
    assert {"액젓", "새우"} <= set(items)


def test_absent_composite_drops_only_paths_through_it():  # §7-7-c
    k = knowledge(recipe=(1, 2, 4))  # 액젓 also listed directly
    items = by_name(query_ontology(request(k=k, confirmed=[(1, "absent")]), now=NOW))
    assert "김치" not in items
    assert items["액젓"].depth == 0  # the direct one stays
    assert "새우" in items  # reached under the direct 액젓


def test_absent_composite_path_removed():
    items = by_name(query_ontology(request(confirmed=[(1, "absent")]), now=NOW))
    assert {"김치", "액젓", "새우"}.isdisjoint(items)


def test_cache_command_holds_full_list():  # §7-7-d
    res = query_ontology(request(confirmed=[(1, "present")]), now=NOW)
    snapshot = res.cache_command.payload["ingredients_snapshot"]
    assert "김치" in {row["ingredient"]["canonical_name"] for row in snapshot}
    assert all("anomaly_locked" not in row for row in snapshot)
    assert res.cache_command.action == "save_menu_ingredient_cache"
    assert res.cache_command.payload["menu_revision"] == 7


def test_cache_key_is_content_based():
    a = query_ontology(request(), now=NOW).cache_command
    b = query_ontology(request(confirmed=[(4, "present")]), now=NOW).cache_command
    assert a.idempotency_key == b.idempotency_key


def test_anomaly_lock_is_kept():  # §7-8, 16
    items = by_name(query_ontology(request(locked=[4]), now=NOW))
    assert items["돼지고기"].anomaly_locked is True
    assert items["두부"].anomaly_locked is False


def test_unknown_taxonomy_ingredient_is_kept():  # §7-10
    res = query_ontology(request(g=graph(drop=(5,))), now=NOW)
    item = by_name(res)["ingredient:5"]
    assert item.taxonomy_category == "unknown"
    assert "constraint_tags_lookup_failed" in codes(res)


def test_broken_base_menu_warns():  # §7-11
    k = knowledge(base_menu_id=1001, remain_token="차돌")  # points at itself
    res = query_ontology(request(k=k), now=NOW)
    assert res.menu.variant_origin is None
    assert "broken_base_menu" in codes(res)


def test_observations_per_corpus_with_zero_fill():  # §7-12, 12-b
    items = by_name(query_ontology(request(), now=NOW))
    pork = {o.corpus: (o.k_count, o.n_total) for o in items["돼지고기"].observations}
    assert pork == {"10000recipe": (8, 10), "semie": (0, 5)}
    assert items["돼지고기"].observation_basis == "direct"


def test_corpus_only_ingredient_is_seed_not_curated():  # §7-12-c
    garlic = by_name(query_ontology(request(), now=NOW))["마늘"]
    assert garlic.depth == 0 and garlic.curated is False
    assert by_name(query_ontology(request(), now=NOW))["김치"].curated is True


def test_hidden_ingredient_inherits_parent_counts():  # §7-21
    shrimp = by_name(query_ontology(request(), now=NOW))["새우"]
    counts = {o.corpus: o.k_count for o in shrimp.observations}
    assert counts["10000recipe"] == 10  # not 0
    assert shrimp.observation_basis == "inherited"


def test_menu_flags_pass_through():  # §7-22
    res = query_ontology(request(), now=NOW)
    assert res.menu_ambiguity_flags == ["has_unclear_jeotgal"]


def test_valid_cache_skips_cache_command():  # §7-20, 24
    assert query_ontology(request(k=knowledge(cache_revision=7)), now=NOW).cache_command is None
    assert query_ontology(request(k=knowledge(cache_revision=6)), now=NOW).cache_command is not None


def test_role_rules():  # §7-23
    k = knowledge(
        recipe=(9, 8, 4, 1),
        corpora=(("10000recipe", 10),),
        priors=(("10000recipe", 8, 10), ("10000recipe", 4, 3)),
    )
    items = by_name(query_ontology(request(name="참치김밥", candidate_name="참치김밥", k=k), now=NOW))
    assert items["참치"].role == "identity"  # in the menu name
    assert items["밥"].role == "identity"  # in 90%+ of recipes
    assert items["돼지고기"].role == "component"
    assert items["액젓"].role == "hidden"


def test_empty_recipe_warns():  # §7-18
    res = query_ontology(request(k=knowledge(recipe=(), priors=())), now=NOW)
    assert "empty_recipe" in codes(res)


def test_tool_keeps_context_and_input_warnings():  # §7-17
    upstream = NodeWarning(
        code="store_context_missing", node="supervisor", item_id=ITEM_ID,
        message="storeless", forces_caution=True,
    )
    req = request(store_id=None, warnings=[upstream])
    res = asyncio.run(OntologyTool().run(req))
    assert res.context == req.context
    assert codes(res)[0] == "store_context_missing"
