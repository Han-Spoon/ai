"""⑥ stage 2 cases from docs/6-agent-websearch.md §4 (3, 4, 5, 15, 17) and §2-6 rules."""

import asyncio

from ai_supervisor.contracts.web_search import WebAggregateRequest
from ai_web_search_agent.aggregate import WebAggregateAgent, aggregate_sources

BEEF = {"ingredient_id": 120, "canonical_name": "소고기"}
KIMCHI = {"ingredient_id": 109, "canonical_name": "김치"}
TOFU = {"ingredient_id": 130, "canonical_name": "두부"}
ONION = {"ingredient_id": 140, "canonical_name": "양파"}
PEANUT = {"ingredient_id": 150, "canonical_name": "땅콩"}
MALA_SAUCE = {"ingredient_id": None, "canonical_name": "마라소스"}
TAGS = {120: ["is_beef"], 150: ["is_peanut"]}
SITE_URL = {
    "10000recipe": "https://www.10000recipe.com/recipe/",
    "wtable": "https://wtable.co.kr/recipes/",
    "semie": "https://semie.cooking/recipe/",
    "web_search": "https://blog.example.com/post/",
}


def mapped(ingredient: dict) -> dict:
    return {
        "ingredient": ingredient,
        "constraint_tags": TAGS.get(ingredient["ingredient_id"], []),
        "role": "identity" if ingredient is BEEF else None,
    }


def source(
    n: int,
    corpus: str,
    ingredients: list[dict],
    *,
    title: str = "김치찌개 황금레시피",
    origin: str = "new",
) -> dict:
    url = f"{SITE_URL[corpus]}{n}"
    return {
        "source_url": url,
        "title": title,
        "source_origin": origin,
        "corpus": corpus,
        "evidence_id": f"web:{url}",
        "ingredients": [mapped(i) for i in ingredients],
    }


def request(sources: list[dict], menu: str = "김치찌개", warnings: list[dict] | None = None) -> WebAggregateRequest:
    return WebAggregateRequest.model_validate(
        {
            "context": {
                "schema_version": "3.0",
                "trace_id": "0199a2f0-0000-7000-8000-000000000001",
                "scan_session_id": "scan-123",
                "store_id": 123456,
                "call_scope": "item",
                "item_id": "scan-123:0",
            },
            "menu": {"menu_id": None, "normalized_menu_name": menu},
            "mapped_sources": sources,
            "warnings": warnings or [],
            "errors": [],
        }
    )


def ten_kimchi_stew_recipes(**extra) -> list[dict]:
    return [source(i, "10000recipe", [KIMCHI, TOFU, ONION], **extra) for i in range(10)]


def observations(outcome, ingredient: dict) -> dict[str, tuple[int, int]]:
    row = next(i for i in outcome.ingredients if i.ingredient.canonical_name == ingredient["canonical_name"])
    return {o.corpus: (o.k_count, o.n_total) for o in row.observations}


def rule_codes(outcome) -> list[str]:
    return [note.rule_code for note in outcome.review_notes]


def test_03_counts_per_site_including_zero() -> None:
    sources = (
        [source(i, "10000recipe", [BEEF if i < 3 else KIMCHI]) for i in range(5)]
        + [source(i, "wtable", [BEEF if i < 1 else KIMCHI]) for i in range(3)]
        + [source(i, "web_search", [KIMCHI]) for i in range(2)]
    )
    outcome = aggregate_sources(request(sources))
    assert observations(outcome, BEEF) == {"10000recipe": (3, 5), "wtable": (1, 3), "web_search": (0, 2)}
    beef = next(i for i in outcome.ingredients if i.ingredient.ingredient_id == 120)
    assert beef.constraint_tags == ["is_beef"]
    assert beef.role == "identity"
    assert (beef.source, beef.depth, beef.curated, beef.observation_basis, beef.anomaly_locked) == (
        "web_search", 0, False, "direct", False,
    )
    assert len(beef.evidence_refs) == 4


def test_04_identical_recipes_each_count() -> None:
    outcome = aggregate_sources(request(ten_kimchi_stew_recipes()))
    assert observations(outcome, KIMCHI) == {"10000recipe": (10, 10)}
    assert outcome.warnings == []


def test_05_fewer_than_ten_keeps_result_with_warning() -> None:
    outcome = aggregate_sources(request(ten_kimchi_stew_recipes()[:8]))
    assert outcome.found is True
    assert [(w.code, w.forces_caution) for w in outcome.warnings] == [("insufficient_sources", True)]


def test_recipe_without_ingredients_is_not_counted() -> None:
    sources = ten_kimchi_stew_recipes() + [source(99, "10000recipe", [])]
    outcome = aggregate_sources(request(sources))
    assert observations(outcome, KIMCHI) == {"10000recipe": (10, 10)}


def test_nothing_found_is_found_false() -> None:
    outcome = aggregate_sources(request([source(0, "10000recipe", [])]))
    assert (outcome.found, outcome.ingredients, outcome.review_notes) == (False, [], [])


def test_unmapped_ingredient_is_kept_and_flagged() -> None:
    sources = ten_kimchi_stew_recipes()
    sources[0]["ingredients"].append(mapped(MALA_SAUCE))
    outcome = aggregate_sources(request(sources))
    sauce = next(i for i in outcome.ingredients if i.ingredient.canonical_name == "마라소스")
    assert sauce.ingredient.ingredient_id is None
    assert "unmapped_ingredient" in rule_codes(outcome)


def test_15_risky_ingredient_in_few_recipes_is_reported() -> None:
    sources = [
        source(i, "10000recipe", [KIMCHI, TOFU, ONION] + ([PEANUT] if i < 3 else []))
        for i in range(22)
    ]
    outcome = aggregate_sources(request(sources))
    risky = next(n for n in outcome.review_notes if n.rule_code == "risky_ingredient_partial")
    assert "땅콩 3/22" in risky.message


def test_17_cache_counts_but_notes_cover_new_recipes_only() -> None:
    cached = [source(i, "10000recipe", [KIMCHI, TOFU, ONION], origin="cache", title="김치찜") for i in range(10)]
    new = [source(i, "wtable", [KIMCHI, TOFU, ONION]) for i in range(10, 20)]
    outcome = aggregate_sources(request(cached + new))
    assert observations(outcome, KIMCHI) == {"10000recipe": (10, 10), "wtable": (10, 10)}
    assert "title_mismatch" not in rule_codes(outcome)
    assert outcome.warnings == []


def test_title_mismatch_flags_other_dish() -> None:
    sources = ten_kimchi_stew_recipes()
    sources[0]["title"] = "김치찜 만들기"
    outcome = aggregate_sources(request(sources))
    note = next(n for n in outcome.review_notes if n.rule_code == "title_mismatch")
    assert "김치찜 만들기" in note.message


def test_title_match_ignores_spacing() -> None:
    outcome = aggregate_sources(request(ten_kimchi_stew_recipes(title="엄마표 김치 찌개")))
    assert "title_mismatch" not in rule_codes(outcome)


def test_general_web_source_is_flagged_by_domain() -> None:
    sources = ten_kimchi_stew_recipes()
    sources[-1] = source(9, "web_search", [KIMCHI, TOFU, ONION])
    outcome = aggregate_sources(request(sources))
    note = next(n for n in outcome.review_notes if n.rule_code == "general_web_source")
    assert "blog.example.com" in note.message


def test_inconsistent_and_rare_rules() -> None:
    # Only 김치 and 두부 reach half of the recipes; 양파 appears in just two.
    sources = [
        source(i, "10000recipe", [KIMCHI if i % 2 else TOFU] + ([ONION] if i < 2 else []))
        for i in range(10)
    ]
    codes = rule_codes(aggregate_sources(request(sources)))
    assert "inconsistent_recipes" in codes
    assert "rare_ingredient" in codes


def test_notes_follow_a_b_c_order() -> None:
    sources = [source(i, "10000recipe", [KIMCHI]) for i in range(10)]
    sources[0] = source(0, "web_search", [KIMCHI, PEANUT, MALA_SAUCE], title="김치찜")
    codes = rule_codes(aggregate_sources(request(sources)))
    assert codes == [
        "general_web_source",
        "title_mismatch",
        "inconsistent_recipes",
        "rare_ingredient",
        "unmapped_ingredient",
        "risky_ingredient_partial",
    ]


def test_clean_batch_has_no_notes() -> None:
    assert aggregate_sources(request(ten_kimchi_stew_recipes())).review_notes == []


def test_run_returns_contract_response_and_keeps_input_warnings() -> None:
    unmapped = {
        "code": "unmapped_constraint_tags",
        "node": "ontology",
        "item_id": "scan-123:0",
        "message": "마라소스",
        "forces_caution": True,
    }
    req = request(ten_kimchi_stew_recipes()[:8], warnings=[unmapped])
    response = asyncio.run(WebAggregateAgent().run(req))
    assert response.context == req.context
    assert response.menu == req.menu
    assert response.found is True
    assert [w.code for w in response.warnings] == ["unmapped_constraint_tags", "insufficient_sources"]
    assert "sources" not in response.model_dump()


def test_run_with_nothing_found() -> None:
    response = asyncio.run(WebAggregateAgent().run(request([source(0, "10000recipe", [])])))
    assert (response.found, response.ingredients, response.review_notes) == (False, [], [])
