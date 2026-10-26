import asyncio

import pytest

from ai_ontology import IngredientMappingTool, map_ingredients
from ai_supervisor.contracts.common import NodeContext, NodeWarning
from ai_supervisor.contracts.external import IngredientGraph
from ai_supervisor.contracts.ontology import IngredientMappingRequest

ITEM_ID = "scan-123:0"

# 1 김치 → 2 멸치액젓 → 3 멸치
INGREDIENTS = {1: "김치", 2: "멸치액젓", 3: "멸치", 4: "소고기", 5: "마늘", 6: "간장", 7: "돼지고기"}


def graph(status="ok"):
    if status != "ok":
        return IngredientGraph(
            status=status, ingredients=[], aliases=[], tags=[], compositions=[], composition_tags=[]
        )
    return IngredientGraph(
        status="ok",
        ingredients=[
            {
                "ingredient_id": i,
                "canonical_name": name,
                "name_normalized": name,
                "localized_name": None,
                "taxonomy_category": "base",
            }
            for i, name in INGREDIENTS.items()
        ],
        aliases=[{"ingredient_id": 4, "alias_normalized": "쇠고기"}],
        tags=[
            {"ingredient_id": 3, "tag_code": "is_fish"},
            {"ingredient_id": 4, "tag_code": "is_beef"},
            {"ingredient_id": 6, "tag_code": "is_soybean"},
            {"ingredient_id": 7, "tag_code": "is_pork"},
        ],
        compositions=[
            {"parent_ingredient_id": 1, "child_ingredient_id": 2, "ambiguity_flag": None},
            {"parent_ingredient_id": 2, "child_ingredient_id": 3, "ambiguity_flag": None},
        ],
        composition_tags=[],
    )


def mapped(names, g=None):
    out = map_ingredients(names, g or graph(), ITEM_ID)
    return {r.input_name: r for r in out.results}, [w.code for w in out.warnings]


def test_exact_name_and_alias():
    results, warnings = mapped(["마늘", "쇠고기 "])
    assert results["마늘"].ingredient.ingredient_id == 5
    assert results["쇠고기 "].ingredient.canonical_name == "소고기"
    assert results["쇠고기 "].constraint_tags == ["is_beef"]
    assert warnings == []


@pytest.mark.parametrize(
    "raw, expected",
    [("다진 마늘 1큰술", 5), ("국간장 2T", 6), ("돼지 앞다리살 300g", 7)],
)
def test_crawling_rules_clean_quantity_and_canonicalize(raw, expected):
    results, _ = mapped([raw])
    assert results[raw].ingredient.ingredient_id == expected


def test_composite_carries_sub_ingredient_tags():
    # web path never expands; 김치 must still bring is_fish (FN)
    results, _ = mapped(["김치"])
    assert results["김치"].constraint_tags == ["is_fish"]


def test_unmapped_name_kept_with_warning():
    results, warnings = mapped(["트러플소금", "마늘"])
    r = results["트러플소금"]
    assert r.ingredient.ingredient_id is None
    assert r.ingredient.canonical_name == "트러플소금"
    assert r.constraint_tags == []
    assert warnings == ["unmapped_constraint_tags"]


def test_unusable_graph_maps_nothing_and_warns():
    results, warnings = mapped(["마늘"], graph("unavailable"))
    assert results["마늘"].ingredient.ingredient_id is None
    assert "unmapped_constraint_tags" in warnings


def request(names, warnings=()):
    return IngredientMappingRequest(
        context=NodeContext(
            schema_version="3.0",
            trace_id="0199a2f0-0000-7000-8000-000000000001",
            scan_session_id="scan-123",
            store_id=None,
            call_scope="item",
            item_id=ITEM_ID,
        ),
        ingredient_names=list(names),
        ingredient_graph=graph(),
        warnings=list(warnings),
        errors=[],
    )


def test_tool_returns_every_name_in_order():
    req = request(["트러플소금", "마늘", "김치"])
    res = asyncio.run(IngredientMappingTool().run(req))
    assert [r.input_name for r in res.results] == ["트러플소금", "마늘", "김치"]
    assert all(r.role is None for r in res.results)
    assert res.context == req.context


def test_tool_keeps_input_warnings():
    upstream = NodeWarning(
        code="store_context_missing", node="supervisor", item_id=ITEM_ID,
        message="storeless", forces_caution=True,
    )
    res = asyncio.run(IngredientMappingTool().run(request(["마늘"], [upstream])))
    assert [w.code for w in res.warnings] == ["store_context_missing"]
