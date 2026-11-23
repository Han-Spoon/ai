from types import SimpleNamespace

from ai_supervisor.routers import route_after_ontology


def ontology_response(
    *,
    exists_in_db: bool,
    ingredients: list,
    base_menu_id=None,
    empty_recipe=False,
    variant_origin=None,
):
    return SimpleNamespace(
        menu=SimpleNamespace(
            exists_in_db=exists_in_db,
            base_menu_id=base_menu_id,
            variant_origin=variant_origin,
        ),
        ingredients=ingredients,
        warnings=(
            [SimpleNamespace(code="empty_recipe")]
            if empty_recipe
            else []
        ),
    )


def test_registered_menu_with_ingredients_uses_bayesian() -> None:
    response = ontology_response(exists_in_db=True, ingredients=[object()])

    assert route_after_ontology(response) == "bayesian"


def test_registered_menu_without_ingredients_falls_back_to_web() -> None:
    response = ontology_response(
        exists_in_db=True, ingredients=[], empty_recipe=True
    )

    assert route_after_ontology(response) == "web_search"


def test_owner_confirmations_can_leave_no_remaining_db_ingredients() -> None:
    response = ontology_response(exists_in_db=True, ingredients=[])

    assert route_after_ontology(response) == "bayesian"


def test_runtime_variant_with_empty_base_recipe_falls_back_to_web() -> None:
    response = ontology_response(
        exists_in_db=False,
        base_menu_id=1001,
        ingredients=[object()],
        empty_recipe=True,
        variant_origin="runtime_tagged",
    )

    assert route_after_ontology(response) == "web_search"


def test_unregistered_menu_uses_web() -> None:
    response = ontology_response(exists_in_db=False, ingredients=[])

    assert route_after_ontology(response) == "web_search"
