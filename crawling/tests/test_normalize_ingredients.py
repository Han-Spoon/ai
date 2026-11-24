import csv
import sys

from crawling.normalize_ingredients import (
    classify_recipe_groups,
    filter_irrelevant_recipes,
    main as normalize_main,
)
from crawling.recipe_relation import classify_recipe_title


def row(menu: str, title: str, url: str, ingredient: str) -> dict[str, str]:
    return {
        "menu_query": menu,
        "recipe_title": title,
        "recipe_url": url,
        "ingredient_raw": ingredient,
        "ingredient_canonical": ingredient,
    }


def test_title_mismatch_drops_the_whole_recipe():
    rows = [
        row("불고기", "콩나물버섯냉채", "wrong", "소고기"),
        row("불고기", "콩나물버섯냉채", "wrong", "새우"),
        row("불고기", "불고기 만드는 법", "right", "소고기"),
    ]

    kept, reasons = filter_irrelevant_recipes(rows)

    assert [item["recipe_url"] for item in kept] == ["right"]
    assert reasons == {"title_mismatch": 1}


def test_self_ingredient_drops_derivative_recipe_and_its_other_ingredients():
    rows = [
        row("순대", "까르보순대", "derived", "순대"),
        row("순대", "까르보순대", "derived", "생크림"),
        row("순대", "순대 만드는 법", "base", "돼지 소창"),
        row("순대", "순대 만드는 법", "base", "당면"),
    ]

    kept, reasons = filter_irrelevant_recipes(rows)

    assert {item["ingredient_canonical"] for item in kept} == {"돼지 소창", "당면"}
    assert reasons == {"self_ingredient": 1}


def test_other_known_menu_and_other_dish_forms_do_not_enter_base_prior():
    known = {"순대", "오징어 순대", "불고기", "김밥", "잡채", "삼겹살", "된장찌개"}

    assert classify_recipe_title("순대", "초보의 오징어순대", known).relation == "other_dish"
    assert classify_recipe_title("불고기", "불고기김밥", known).relation == "other_dish"
    assert classify_recipe_title("불고기", "불고기김밥", {"불고기"}).relation == "other_dish"
    assert classify_recipe_title("잡채", "잡채볶음밥", known).relation == "other_dish"
    assert classify_recipe_title("순대", "순대쌈장 만들기", known).relation == "other_dish"
    assert classify_recipe_title("불고기", "불고기 상추쌈밥", known).relation == "other_dish"
    assert classify_recipe_title("불고기", "쌀밥과 김치 우둔살 불고기", known).relation == "other_dish"
    assert classify_recipe_title("삼겹살", "삼겹살 김치찜", known).relation == "other_dish"
    assert classify_recipe_title("삼겹살", "대패 삼겹살 두루치기", known).relation == "other_dish"
    assert classify_recipe_title("삼겹살", "삼겹살 된장찌게", known).relation == "other_dish"


def test_ingredient_modifier_is_variant_but_same_menu_alias_is_exact():
    known = {"순대", "계란찜"}

    variant = classify_recipe_title("순대", "가지순대 만드는 법", known)
    alias = classify_recipe_title("계란찜", "부드러운 달걀찜 만드는 법", known)

    assert (variant.relation, variant.base_menu_name, variant.remain_token) == (
        "variant",
        "순대",
        "가지",
    )
    assert alias.relation == "exact"


def test_spaced_seaweed_modifier_does_not_enter_base_gimbap_prior():
    known = {"김밥"}

    seaweed = classify_recipe_title("김밥", "해초 김밥", known)
    hijiki = classify_recipe_title("김밥", "톳 김밥", known)
    base = classify_recipe_title("김밥", "김밥 맛있게 싸는 법", known)

    assert (seaweed.relation, seaweed.base_menu_name, seaweed.remain_token) == (
        "variant",
        "김밥",
        "해초",
    )
    assert (hijiki.relation, hijiki.base_menu_name, hijiki.remain_token) == (
        "variant",
        "김밥",
        "톳",
    )
    assert base.relation == "exact"


def test_finished_dish_phrase_drops_whole_recipe_and_is_auditable():
    rows = [
        row("잡채", "남은 잡채 활용", "derived", "완성된 잡채"),
        row("잡채", "남은 잡채 활용", "derived", "밥"),
        row("잡채", "잡채 만드는 법", "base", "당면"),
    ]

    kept, reasons, audit = classify_recipe_groups(rows)

    assert [item["recipe_url"] for item in kept] == ["base"]
    assert reasons == {"derivative": 1}
    rejected = next(item for item in audit if item["recipe_url"] == "derived")
    assert rejected["included_in_base_prior"] == "false"
    assert rejected["reason"] == "finished_dish_used_as_ingredient"


def test_raw_ingredient_alias_is_not_mistaken_for_finished_dish():
    rows = [
        row("막창구이", "에어프라이어 막창구이 만들기", "base", "막창"),
        row("막창구이", "에어프라이어 막창구이 만들기", "base", "소금"),
    ]

    kept, reasons, audit = classify_recipe_groups(rows)

    assert {item["ingredient_canonical"] for item in kept} == {"막창", "소금"}
    assert not reasons
    assert audit[0]["relation"] == "exact"


def test_normalized_vocabulary_is_kept_while_prior_uses_only_exact_recipes(
    tmp_path, monkeypatch
):
    source = tmp_path / "recipes.csv"
    with source.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["menu_query", "recipe_title", "recipe_url", "ingredient_name"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "menu_query": "순대",
                    "recipe_title": "순대 만드는 법",
                    "recipe_url": "base",
                    "ingredient_name": "돼지고기",
                },
                {
                    "menu_query": "순대",
                    "recipe_title": "까르보순대",
                    "recipe_url": "derived",
                    "ingredient_name": "순대",
                },
                {
                    "menu_query": "순대",
                    "recipe_title": "까르보순대",
                    "recipe_url": "derived",
                    "ingredient_name": "생크림",
                },
            ]
        )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "normalize_ingredients.py",
            str(source),
            "--out-prefix",
            "result",
            "--out-dir",
            str(tmp_path),
        ],
    )
    normalize_main()

    with (tmp_path / "result_normalized.csv").open(encoding="utf-8-sig") as f:
        normalized = list(csv.DictReader(f))
    with (tmp_path / "result_prior.csv").open(encoding="utf-8-sig") as f:
        prior = list(csv.DictReader(f))
    with (tmp_path / "result_recipe_audit.csv").open(encoding="utf-8-sig") as f:
        audit = list(csv.DictReader(f))

    assert {row["ingredient_canonical"] for row in normalized} == {
        "돼지고기",
        "순대",
        "생크림",
    }
    assert [(row["ingredient_canonical"], row["n_total"], row["k_count"]) for row in prior] == [
        ("돼지고기", "1", "1")
    ]
    rejected = next(row for row in audit if row["recipe_url"] == "derived")
    assert rejected["included_in_base_prior"] == "false"
