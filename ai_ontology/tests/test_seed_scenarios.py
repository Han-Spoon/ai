"""④→⑤ on the real seed (crawling CSVs + curated menus + hidden rules), #186 QA.

Fake graphs in the unit tests cannot catch seed problems: a missing alias, a path tag
moving onto the wrong ingredient, a dictionary gap. These run the real data.
"""

import csv

import pytest

from ai_bayesian import estimate_probabilities
from ai_ontology import map_ingredients, query_ontology
from ai_ruleengine.constants import ALL_TAGS
from ai_supervisor.contracts.bayesian import BayesianRequest
from ai_supervisor.contracts.ontology import OntologyRequest

from .seed import ROOT, seed

CONTEXT = {
    "schema_version": "3.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "scan-qa",
    "store_id": 1,
    "call_scope": "item",
    "item_id": "scan-qa:0",
}


def ontology(name, menu=None, *, store_id=1, residual=()):
    s = seed()
    menu = menu or name
    menu_id = s.menu_id[menu]
    return query_ontology(
        OntologyRequest.model_validate(
            {
                "context": {**CONTEXT, "store_id": store_id},
                "menu": {
                    "menu_id": menu_id,
                    "normalized_menu_name": name,
                    "match_candidates": [
                        {
                            "menu_id": menu_id,
                            "menu_category_id": None,
                            "canonical_name": menu,
                            "score": 1.0,
                            "match_type": "canonical",
                        }
                    ],
                    "residual_tokens": list(residual),
                },
                "unconfirmed_only": True,
                "confirmed_ingredients": [],
                "knowledge": s.ontology(menu),
                "ingredient_graph": s.graph,
                "warnings": [],
                "errors": [],
            }
        )
    )


def probabilities(response):
    fields = ("ingredient", "constraint_tags", "role", "source", "depth", "curated",
              "observation_basis", "observations", "evidence_refs")
    request = BayesianRequest.model_validate(
        {
            "context": CONTEXT,
            "menu_category_id": response.menu.menu_category_id,
            "ingredients": [{f: getattr(i, f) for f in fields} for i in response.ingredients],
            "priors": {"status": "not_found", "store": [], "cluster": [], "global": [], "evidence": []},
            "source_reliability": {"status": "not_found", "weights": []},
            "warnings": [],
            "errors": [],
        }
    )
    return estimate_probabilities(request).probabilities


def entries(response, name):
    return [i for i in response.ingredients if i.ingredient.canonical_name == name]


def tags(response):
    return {t for i in response.ingredients for t in i.constraint_tags}


def test_seed_matches_db_export():
    g = seed().graph
    # 325 ingredients (324 as first loaded + 돼지감자 from the 22 Yongsan menus, 2026-11-22);
    # 101 edges minus 9 from dish-parent rules (김밥→밥 etc.)
    assert (len(g.ingredients), len(g.compositions)) == (325, 92)


def test_no_composition_hangs_under_a_dish_turned_raw_ingredient():
    # 김밥→밥, 열무김치→열무, 김칫잎→김 made "fish cake in rice" before the fix
    s = seed()
    for parent, child in [("밥", "맛살"), ("밥", "햄"), ("열무", "새우젓"), ("김", "멸치액젓"), ("조개", "생선")]:
        assert (parent, child) not in s.edges


def test_rice_and_seaweed_carry_no_seafood_tags():
    s = seed()
    from ai_ontology import lookup_tags
    out = lookup_tags([s.id["밥"], s.id["김"], s.id["열무"]], s.graph, "x")
    for name in ("밥", "김", "열무"):
        assert not {"is_fish", "is_shellfish"} & set(out.tags[s.id[name]]), name


# ── 메뉴별 시나리오 ──────────────────────────────────────────────


def test_kimchi_jjigae_carries_fish_and_shellfish_for_allergy_users():
    res = ontology("김치찌개")
    assert {"is_fish", "is_shellfish", "is_pork"} <= tags(res)
    assert any("is_shrimp" in i.constraint_tags for i in entries(res, "새우젓"))
    assert any("is_fish" in i.constraint_tags for i in entries(res, "멸치액젓"))  # not on 새우젓
    assert res.menu_ambiguity_flags  # 젓갈·육수 불분명 표시가 ⑧까지 간다


def test_doenjang_jjigae_soybean_is_near_certain():
    res = ontology("된장찌개")
    doenjang = next(p for p in probabilities(res) if p.ingredient.canonical_name == "된장")
    assert "is_soybean" in doenjang.constraint_tags
    assert doenjang.posterior_mean > 0.9
    assert doenjang.prior_source == "global"


def test_galbi_garlic_matches_crawling_counts():  # ⑤ §7-1
    res = ontology("갈비구이")
    garlic = entries(res, "마늘")[0]
    # 2026-11-22: 갈비구이 10000recipe grew from 57 to 210 recipes (crawling/README.md)
    assert ("10000recipe", 182, 210) in [(o.corpus, o.k_count, o.n_total) for o in garlic.observations]


def test_samgyeopsal_pork_for_halal():
    res = ontology("삼겹살")
    pork = next(p for p in probabilities(res) if p.ingredient.canonical_name == "돼지고기")
    assert pork.role == "identity" and pork.posterior_mean > 0.9


def test_chadol_variant_maps_to_beef_without_alias_rows():
    res = ontology("차돌된장찌개", "된장찌개")
    assert res.menu.variant_origin == "runtime_tagged" and res.unmapped_tokens == []
    beef = [i for i in entries(res, "소고기") if i.source == "variant_suggested"]
    assert beef and beef[0].constraint_tags == ["is_beef"] and beef[0].observations == []
    assert res.cache_command is None


def test_cheese_option_token_is_added():
    res = ontology("김치찌개", residual=["치즈"])
    assert any(i.source == "variant_suggested" for i in entries(res, "치즈"))
    assert "is_milk" in tags(res)


def test_path_tag_does_not_leak_onto_plain_ingredient():
    # 삼겹살 corpus has plain 식초 and 마요네즈 -> 식초 / 계란: vinegar never gets the egg
    res = ontology("삼겹살")
    assert all("is_egg" not in i.constraint_tags for i in entries(res, "식초"))
    assert any("is_egg" in i.constraint_tags for i in entries(res, "마요네즈"))


def test_storeless_scan_keeps_tags_without_cache():
    res = ontology("김치찌개", store_id=None)
    assert "is_fish" in tags(res) and res.cache_command is None


# ── 전체 큐레이션 메뉴 ──────────────────────────────────────────


@pytest.mark.parametrize("menu", sorted(seed().recipe))
def test_every_curated_menu_expands_safely(menu):
    res = ontology(menu)
    names = {i.ingredient.canonical_name for i in res.ingredients}
    assert set(seed().recipe[menu]) <= names, "curated recipe ingredient dropped (FN)"
    assert not {w.code for w in res.warnings} & {"cycle_detected", "max_depth_reached", "constraint_tags_lookup_failed"}
    assert tags(res) <= set(ALL_TAGS)
    for p in probabilities(res):
        assert 0 < p.posterior_mean < 1
        assert p.prior_source in {"global", "uninformative"}


# ── 웹 재료 매핑 ────────────────────────────────────────────────


def test_web_names_from_real_recipes_mostly_map():
    with open(ROOT / "crawling/10000recipe_recipes_raw.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    names = list(dict.fromkeys(r["ingredient_name"] for r in rows))
    out = map_ingredients(names, seed().graph, "scan-qa:0")
    mapped = {r.input_name for r in out.results if r.ingredient.ingredient_id is not None}
    counts = [r["ingredient_name"] in mapped for r in rows]
    assert len(mapped) / len(names) >= 0.9
    assert sum(counts) / len(counts) >= 0.98
    assert any(w.code == "unmapped_constraint_tags" for w in out.warnings)  # misses block SAFE


@pytest.mark.parametrize(
    "name, absent, present",
    [
        ("땅콩", "is_soybean", "is_peanut"),
        ("강낭콩", "is_soybean", None),
        ("굴비", "is_shellfish", "is_fish"),
        ("둥굴레", "is_shellfish", None),
        ("멍게", "is_crab", None),
        ("밀크초콜릿", "is_wheat", "is_milk"),
        ("햄버거번", "is_pork", "is_wheat"),
        ("다시다", None, "is_beef"),
        ("젓갈", None, "is_shrimp"),
        ("곱창", None, "is_pork"),
        ("와인", None, "is_sulfite"),
    ],
)
def test_keyword_tag_fixes(name, absent, present):
    tags = set(seed().data.tags[name])
    if absent:
        assert absent not in tags
    if present:
        assert present in tags


@pytest.mark.parametrize(
    "parent, child",
    [("햄", "소금"), ("햄", "아질산나트륨"), ("만두", "부추"), ("어묵", "전분"), ("육수", "다시마"), ("양념장", "참기름")],
)
def test_path_tag_is_not_put_on_a_plain_child(parent, child):
    # salt in ham has no pork: 계란말이 asked "salt?" for pork at 72% and kelp for beef
    s = seed()
    assert (parent, child) in s.edges
    assert not [t for p, c, t in s.edge_tags if (p, c) == (parent, child)]


def test_parent_keeps_every_hidden_rule_tag():
    # moving a path tag onto the owning sibling must not drop it from the parent (FN)
    import db_seed

    s = seed()
    kept = {(p, t) for p, _, t in s.edge_tags}
    dropped = {raw for raw, _ in s.data.dropped_rules}
    for parent, flags in db_seed.HIDDEN_RULES.items():
        p = db_seed.canon(parent)
        if parent in dropped or not p:
            continue
        for chs in flags.values():
            for ch in chs:
                c = db_seed.canon(ch["name"])
                if c and c != p:
                    assert (p, ch["tag"]) in kept, (parent, ch)


def test_path_tag_sits_on_an_owning_child_when_there_is_one():
    s = seed()
    owned = {(p, t) for p, c, t in s.edge_tags if t in s.data.tags[c]}
    stray = [(p, c, t) for p, c, t in s.edge_tags if (p, t) in owned and t not in s.data.tags[c]]
    assert not stray


@pytest.mark.parametrize("menu", ["간장게장", "약과"])
def test_other_dish_bundle_is_left_out(menu):
    # wtable 간장게장: 911 recipes, 907 soy sauce and 3 crab; wtable 약과: one ice-cream recipe
    assert "wtable" not in [c.corpus for c in seed().ontology(menu).menu_recipe_corpora]


def test_dropped_bundle_keeps_ingredient_ids():
    # ids are list positions; 아이스크림 only came from the dropped 약과 bundle
    s = seed()
    assert "아이스크림" in s.id and len(s.id) == 325


def test_sauce_name_does_not_map_to_vegetables():
    out = map_ingredients(["잡채소스"], seed().graph, "x")
    assert out.results[0].ingredient.canonical_name != "채소"


def test_absent_sub_ingredient_is_not_read_for_a_present_parent():
    # owner: "배추김치 present, 새우젓·새우 absent" gave 배추김치 the shrimp tag through
    # 새우젓, so a shrimp-allergic guest got DANGER (owner-answer-only route)
    from ai_ontology import TagLookupTool
    from ai_supervisor.contracts.ontology import TagLookupRequest
    import asyncio

    s = seed()
    kimchi, jeot, shrimp = s.id["배추김치"], s.id["새우젓"], s.id["새우"]
    request = TagLookupRequest(
        context={"schema_version": "3.0", "trace_id": "0199a2f0-0000-7000-8000-000000000001",
                 "scan_session_id": "x", "store_id": 1, "call_scope": "item", "item_id": "x:0"},
        ingredient_ids=[kimchi, jeot, shrimp],
        ingredient_graph=s.graph,
        absent_ingredient_ids=[jeot, shrimp],
        warnings=[],
        errors=[],
    )
    tags = {r.ingredient_id: r.constraint_tags for r in asyncio.run(TagLookupTool().run(request)).results}
    assert "is_shrimp" not in tags[kimchi]
    # 멸치액젓 was not answered: its fish and the kimchi -> 멸치액젓 shellfish path stay (no FN)
    assert {"is_fish", "is_shellfish"} <= set(tags[kimchi])
    assert "is_shrimp" in tags[jeot]  # the absent answer itself keeps its tags


def test_without_absent_answers_the_parent_keeps_sub_ingredient_tags():
    s = seed()
    from ai_ontology import lookup_tags

    assert "is_shrimp" in lookup_tags([s.id["배추김치"]], s.graph, "x").tags[s.id["배추김치"]]
