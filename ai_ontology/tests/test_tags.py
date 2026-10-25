import asyncio

import pytest

from ai_ontology import MAX_DEPTH, TagLookupTool, lookup_tags
from ai_supervisor.contracts.common import NodeContext, NodeWarning
from ai_supervisor.contracts.external import IngredientGraph
from ai_supervisor.contracts.ontology import TagLookupRequest

ITEM_ID = "scan-123:0"

# 1 김치 → 2 멸치액젓 → 3 멸치, 4 돼지고기, 5 두부
NAMES = {1: "김치", 2: "멸치액젓", 3: "멸치", 4: "돼지고기", 5: "두부"}


def graph(
    *,
    tags=((3, "is_fish"), (4, "is_pork"), (5, "is_soybean")),
    compositions=((1, 2), (2, 3)),
    composition_tags=((1, 2, "is_shellfish"),),
    ingredients=NAMES,
    status="ok",
) -> IngredientGraph:
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
            for i, name in ingredients.items()
        ],
        aliases=[],
        tags=[{"ingredient_id": i, "tag_code": t} for i, t in tags],
        compositions=[
            {"parent_ingredient_id": p, "child_ingredient_id": c, "ambiguity_flag": None}
            for p, c in compositions
        ],
        composition_tags=[
            {"parent_ingredient_id": p, "child_ingredient_id": c, "tag_code": t}
            for p, c, t in composition_tags
        ],
    )


def codes(warnings: list[NodeWarning]) -> list[str]:
    return [w.code for w in warnings]


def test_node_tags_only_for_leaf_ingredient():
    out = lookup_tags([4, 5], graph(), ITEM_ID)
    assert out.tags == {4: ["is_pork"], 5: ["is_soybean"]}
    assert out.warnings == []


def test_confirmed_parent_carries_sub_ingredient_and_path_tags():
    # 김치 present means 멸치액젓·멸치 are in it too; dropping them would hide is_fish (FN)
    out = lookup_tags([1], graph(), ITEM_ID)
    assert out.tags[1] == ["is_fish", "is_shellfish"]


def test_ingredient_without_tags_is_normal_empty():
    out = lookup_tags([5], graph(tags=()), ITEM_ID)
    assert out.tags[5] == []
    assert out.warnings == []


def test_unknown_ingredient_warns_instead_of_silent_empty():
    out = lookup_tags([99], graph(), ITEM_ID)
    assert out.tags[99] == []
    assert codes(out.warnings) == ["constraint_tags_lookup_failed"]
    assert out.warnings[0].forces_caution is True


@pytest.mark.parametrize("status", ["not_found", "unavailable"])
def test_unusable_graph_warns_for_all(status):
    out = lookup_tags([1, 4], graph(status=status), ITEM_ID)
    assert out.tags == {1: [], 4: []}
    assert codes(out.warnings) == ["constraint_tags_lookup_failed"]


def test_cycle_is_cut_and_warned():
    out = lookup_tags([1], graph(compositions=((1, 2), (2, 1))), ITEM_ID)
    assert "cycle_detected" in codes(out.warnings)
    assert out.tags[1] == ["is_shellfish"]


def test_depth_limit_stops_and_warns():
    chain = {i: f"재료{i}" for i in range(1, MAX_DEPTH + 3)}
    edges = tuple((i, i + 1) for i in range(1, MAX_DEPTH + 2))
    last = MAX_DEPTH + 2
    out = lookup_tags(
        [1],
        graph(ingredients=chain, compositions=edges, composition_tags=(), tags=((MAX_DEPTH + 1, "is_egg"), (last, "is_milk"))),
        ITEM_ID,
    )
    assert "is_egg" in out.tags[1]  # depth 5 is read
    assert "is_milk" not in out.tags[1]  # depth 6 is not
    assert "max_depth_reached" in codes(out.warnings)


def test_depth_limit_without_deeper_rows_does_not_warn():
    chain = {i: f"재료{i}" for i in range(1, MAX_DEPTH + 2)}
    edges = tuple((i, i + 1) for i in range(1, MAX_DEPTH + 1))
    out = lookup_tags([1], graph(ingredients=chain, compositions=edges, composition_tags=()), ITEM_ID)
    assert out.warnings == []


def request(ids, g=None, warnings=()):
    return TagLookupRequest(
        context=NodeContext(
            schema_version="3.0",
            trace_id="0199a2f0-0000-7000-8000-000000000001",
            scan_session_id="scan-123",
            store_id=123456,
            call_scope="item",
            item_id=ITEM_ID,
        ),
        ingredient_ids=list(ids),
        ingredient_graph=g or graph(),
        warnings=list(warnings),
        errors=[],
    )


def test_tool_returns_every_id_in_order_and_keeps_context():
    req = request([5, 1, 4])
    res = asyncio.run(TagLookupTool().run(req))
    assert [r.ingredient_id for r in res.results] == [5, 1, 4]
    assert res.context == req.context
    assert all(r.role is None for r in res.results)


def test_tool_keeps_input_warnings():
    upstream = NodeWarning(
        code="store_context_missing", node="supervisor", item_id=ITEM_ID,
        message="storeless", forces_caution=True,
    )
    res = asyncio.run(TagLookupTool().run(request([99], warnings=[upstream])))
    assert codes(res.warnings) == ["store_context_missing", "constraint_tags_lookup_failed"]
