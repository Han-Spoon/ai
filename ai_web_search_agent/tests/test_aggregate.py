"""⑥ stage 2 cases from docs/6-agent-websearch.md §4 (3, 4, 5, 15, 17, 21) and §2-6 rules."""

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
    assert (beef.source, beef.depth, beef.curated, beef.observation_basis) == (
        "web_search", 0, False, "direct",
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
    assert (outcome.found, outcome.ingredients) == (False, [])


def test_unmapped_ingredient_is_kept_and_flagged() -> None:
    sources = ten_kimchi_stew_recipes()
    for recipe in sources[:2]:
        recipe["ingredients"].append(mapped(MALA_SAUCE))
    outcome = aggregate_sources(request(sources))
    sauce = next(i for i in outcome.ingredients if i.ingredient.canonical_name == "마라소스")
    assert sauce.ingredient.ingredient_id is None


# --- rare ingredients left out of the judgment (§2-2-1, 2026-11-24) ------------


def names(outcome) -> set[str]:
    return {i.ingredient.canonical_name for i in outcome.ingredients}


def test_ingredient_in_one_of_ten_recipes_is_dropped() -> None:
    sources = ten_kimchi_stew_recipes()
    sources[0]["ingredients"].append(mapped(PEANUT))
    assert names(aggregate_sources(request(sources))) == {"김치", "두부", "양파"}


def test_ingredient_in_two_of_ten_recipes_is_kept_with_its_counts() -> None:
    sources = ten_kimchi_stew_recipes()
    for recipe in sources[:2]:
        recipe["ingredients"].append(mapped(PEANUT))
    outcome = aggregate_sources(request(sources))
    assert observations(outcome, PEANUT) == {"10000recipe": (2, 10)}


def test_rare_ingredients_stay_when_their_tag_is_common_but_uncovered() -> None:
    # Peanut appears in 3 of 10 recipes, each time under a different name.
    peanut_names = [
        {"ingredient_id": 151 + n, "canonical_name": name}
        for n, name in enumerate(["땅콩버터", "땅콩가루", "볶은땅콩"])
    ]
    sources = ten_kimchi_stew_recipes()
    for recipe, peanut in zip(sources, peanut_names):
        recipe["ingredients"].append(
            {"ingredient": peanut, "constraint_tags": ["is_peanut"], "role": None}
        )
    assert {"땅콩버터", "땅콩가루", "볶은땅콩"} <= names(aggregate_sources(request(sources)))


def test_rare_ingredient_is_dropped_when_a_common_one_carries_its_tag() -> None:
    sources = ten_kimchi_stew_recipes()
    for recipe in sources[:5]:
        recipe["ingredients"].append(mapped(PEANUT))
    sources[9]["ingredients"].append(
        {"ingredient": {"ingredient_id": 160, "canonical_name": "땅콩버터"}, "constraint_tags": ["is_peanut"], "role": None}
    )
    assert "땅콩버터" not in names(aggregate_sources(request(sources)))


def test_recipes_sharing_nothing_keep_every_ingredient() -> None:
    picks = [KIMCHI, TOFU, ONION, BEEF, PEANUT, MALA_SAUCE]
    sources = [source(i, "10000recipe", [picks[i]]) for i in range(6)]
    outcome = aggregate_sources(request(sources))
    assert outcome.found is True
    assert len(outcome.ingredients) == 6


def test_17_cache_counts_but_notes_cover_new_recipes_only() -> None:
    cached = [source(i, "10000recipe", [KIMCHI, TOFU, ONION], origin="cache", title="김치찜") for i in range(10)]
    new = [source(i, "wtable", [KIMCHI, TOFU, ONION]) for i in range(10, 20)]
    outcome = aggregate_sources(request(cached + new))
    assert observations(outcome, KIMCHI) == {"10000recipe": (10, 10), "wtable": (10, 10)}
    assert outcome.warnings == []


def test_21_title_without_menu_name_is_not_dropped() -> None:
    sources = ten_kimchi_stew_recipes()
    sources[0]["title"] = "엄마표 얼큰 찌개"
    outcome = aggregate_sources(request(sources))
    assert sum(o.n_total for o in outcome.ingredients[0].observations) == 10


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
    assert (response.found, response.ingredients) == (False, [])


# --- recipe signals for auto registration (§5, 2026-11-02) --------------------


def signals(outcome) -> list[tuple[str, str]]:
    return [(s.evidence_id.rsplit("/", 1)[-1], s.code) for s in outcome.recipe_signals]


def test_clean_recipes_have_no_signal_and_pass() -> None:
    assert aggregate_sources(request(ten_kimchi_stew_recipes())).recipe_signals == []


def test_other_dish_is_suspected_when_it_lacks_what_the_others_share() -> None:
    sources = ten_kimchi_stew_recipes()
    sources[3] = source(3, "10000recipe", [BEEF])  # none of 김치·두부·양파
    sources[4] = source(4, "10000recipe", [KIMCHI, BEEF])  # lacks 2 of 3: 67%, under 80%
    outcome = aggregate_sources(request(sources))
    assert signals(outcome) == [("3", "other_dish_suspected")]
    assert "3개 중 3개" in outcome.recipe_signals[0].message


def test_title_and_unmapped_signals_are_per_recipe() -> None:
    sources = ten_kimchi_stew_recipes()
    sources[1]["title"] = "엄마표 얼큰 찌개"
    sources[2] = source(2, "10000recipe", [KIMCHI, TOFU, ONION, MALA_SAUCE])
    outcome = aggregate_sources(request(sources))
    assert signals(outcome) == [("1", "title_mismatch"), ("2", "unmapped_ingredient")]
    assert "마라소스" in outcome.recipe_signals[1].message


def test_generic_unmapped_words_send_no_recipe_to_review() -> None:
    # 2026-11-12 run: 조미료·야채·반죽 alone sent a quarter of the recipes to review.
    generic = [{"ingredient_id": None, "canonical_name": n} for n in ("조미료", "야채", "반죽")]
    meat = {"ingredient_id": None, "canonical_name": "고기"}
    sources = ten_kimchi_stew_recipes()
    sources[2] = source(2, "10000recipe", [KIMCHI, TOFU, ONION, *generic])
    sources[3] = source(3, "10000recipe", [KIMCHI, TOFU, ONION, *generic, meat])
    outcome = aggregate_sources(request(sources))
    # 고기 may be pork or beef, so that recipe still goes to the operator.
    assert signals(outcome) == [("3", "unmapped_ingredient")]
    assert outcome.recipe_signals[0].message.endswith(": 고기")


def test_vague_meat_next_to_a_named_meat_sends_no_recipe_to_review() -> None:
    # 2026-11-12 second run: "고기" in 돼지불백 recipes that list 돼지고기 is that pork.
    pork = {"ingredient_id": 101, "canonical_name": "돼지고기"}
    meat = {"ingredient_id": None, "canonical_name": "고기"}
    sources = ten_kimchi_stew_recipes()
    sources[2] = source(2, "10000recipe", [KIMCHI, TOFU, ONION, pork, meat])
    sources[3] = source(3, "10000recipe", [KIMCHI, TOFU, ONION, BEEF, meat])
    sources[4] = source(4, "10000recipe", [KIMCHI, TOFU, ONION, meat])
    # Only the recipe with no named meat still goes to the operator.
    assert signals(aggregate_sources(request(sources))) == [("4", "unmapped_ingredient")]


def test_signals_cover_new_recipes_only() -> None:
    cached = [source(i, "10000recipe", [BEEF], origin="cache", title="김치찜") for i in range(10)]
    new = [source(i, "wtable", [KIMCHI, TOFU, ONION]) for i in range(10, 20)]
    # Cached recipes were checked when they were new, and they are not compared with.
    assert aggregate_sources(request(cached + new)).recipe_signals == []


def test_run_sends_signals_to_supervisor() -> None:
    sources = ten_kimchi_stew_recipes()
    sources[1]["title"] = "엄마표 얼큰 찌개"
    response = asyncio.run(WebAggregateAgent().run(request(sources)))
    assert signals(response) == [("1", "title_mismatch")]


def test_run_sends_empty_signals_explicitly() -> None:
    # ⓪ reads an omitted field as "review every new recipe"; an empty list means none.
    response = asyncio.run(WebAggregateAgent().run(request(ten_kimchi_stew_recipes())))
    assert response.recipe_signals == []
    assert "recipe_signals" in response.model_fields_set


def test_the_menu_itself_is_not_an_unmapped_ingredient() -> None:
    # 2026-11-17: "잡채" in 잡채 recipes cannot map and sent each one to review.
    dish = {"ingredient_id": None, "canonical_name": "김치찌개"}
    sources = ten_kimchi_stew_recipes()
    sources[2] = source(2, "10000recipe", [KIMCHI, TOFU, ONION, dish])
    assert aggregate_sources(request(sources)).recipe_signals == []


def test_unmapped_names_are_compared_without_their_amounts() -> None:
    # 2026-11-17: the LLM kept "고기100g" and "조미료 약간" as written.
    pork = {"ingredient_id": 101, "canonical_name": "돼지고기"}
    meat = {"ingredient_id": None, "canonical_name": "고기100g"}
    msg = {"ingredient_id": None, "canonical_name": "조미료(생략 가능)"}
    sources = ten_kimchi_stew_recipes()
    sources[2] = source(2, "10000recipe", [KIMCHI, TOFU, ONION, pork, meat, msg])
    assert aggregate_sources(request(sources)).recipe_signals == []
