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
                "anomaly_locked_ingredients": [],
                "knowledge": s.ontology(menu),
                "ingredient_graph": s.graph,
                "warnings": [],
                "errors": [],
            }
        )
    )


def probabilities(response):
    fields = ("ingredient", "constraint_tags", "role", "source", "depth", "curated",
              "observation_basis", "observations", "anomaly_locked", "evidence_refs")
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
    assert (len(g.ingredients), len(g.compositions)) == (324, 101)


# ── 메뉴별 시나리오 ──────────────────────────────────────────────


def test_kimchi_jjigae_carries_fish_and_shellfish_for_allergy_users():
    res = ontology("김치찌개")
    assert {"is_fish", "is_shellfish", "is_pork"} <= tags(res)
    assert any("is_fish" in i.constraint_tags for i in entries(res, "새우젓"))
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
    assert ("10000recipe", 53, 57) in [(o.corpus, o.k_count, o.n_total) for o in garlic.observations]


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
    # 삼겹살 corpus has plain 식초 and 마요네즈→식초 (is_egg path tag)
    res = ontology("삼겹살")
    plain = [i for i in entries(res, "식초") if i.depth == 0]
    assert plain and "is_egg" not in plain[0].constraint_tags
    assert any("is_egg" in i.constraint_tags for i in entries(res, "식초") if i.depth > 0)


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
