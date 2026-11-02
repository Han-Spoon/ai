"""⑥ evaluation against the hand-written menus (evaluation/websearch.py)."""

import asyncio
from datetime import datetime, timezone

import pytest

from ai_web_search_agent import SearchResult, StaticSearchProvider, WebCollectAgent
from evaluation.websearch import (
    Counts,
    GoldMenu,
    MenuRun,
    collect_all,
    f2,
    load_gold,
    load_runs,
    render_report,
    save_runs,
    score,
    tag_names,
    wilson,
)

FETCHED_AT = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)


def source(*ingredients: str) -> dict:
    return {"url": "https://www.10000recipe.com/recipe/1", "title": "t", "ingredients": list(ingredients)}


def test_tags_follow_the_rule_engine_keywords():
    assert tag_names(["꽃게", "노란 콩가루", "진간장"]) == {"is_crab", "is_soybean"}
    assert tag_names(["배추", "소금"]) == set()


def test_spaghetti_is_wheat_not_crab():
    assert tag_names(["스파게티"]) == {"is_wheat"}
    assert tag_names(["스파게티", "꽃게"]) == {"is_wheat", "is_crab"}


def test_gold_menus_are_the_77_hand_written_ones():
    gold = {menu.name: menu for menu in load_gold()}

    assert len(gold) == 77
    assert gold["김치찌개"].allergens == {"is_pork"}
    assert "is_crab" not in gold["부대찌개"].allergens
    assert gold["간장게장"].allergens == {"is_crab", "is_soybean"}


def test_predicted_counts_any_recipe_or_a_share_of_them():
    run = MenuRun("김치찌개", sources=[source("돼지고기", "두부"), source("돼지고기"), source("참치")])

    assert run.predicted(0.0) == {"is_pork", "is_soybean"}
    assert run.predicted(0.5) == {"is_pork"}
    assert MenuRun("없는 메뉴").predicted(0.0) == set()


def test_score_counts_misses_and_extras_per_allergen():
    gold = [
        GoldMenu("김치찌개", ["돼지고기"], {"is_pork"}),
        GoldMenu("간장게장", ["꽃게", "간장"], {"is_crab", "is_soybean"}),
        GoldMenu("못 찾은 메뉴", ["달걀"], {"is_egg"}),
    ]
    runs = {
        "김치찌개": MenuRun("김치찌개", sources=[source("돼지고기", "새우젓")]),
        "간장게장": MenuRun("간장게장", sources=[source("꽃게")]),
    }

    result = score(gold, runs, 0.0)

    assert (result.total.tp, result.total.fn, result.total.fp) == (2, 2, 1)
    assert sorted(result.missed) == [("간장게장", "is_soybean"), ("못 찾은 메뉴", "is_egg")]
    assert result.extra == [("김치찌개", "is_shrimp")]
    assert result.per_allergen["is_egg"].fn == 1


def test_f2_weights_misses_four_times_extras():
    assert f2(Counts(tp=8, fn=2, fp=0)) == pytest.approx(40 / 48)
    assert f2(Counts(tp=8, fn=0, fp=2)) == pytest.approx(40 / 42)
    assert f2(Counts()) == 0.0


def test_wilson_interval():
    low, high = wilson(47, 50)

    assert low == pytest.approx(0.838, abs=0.001)
    assert high == pytest.approx(0.979, abs=0.001)
    assert wilson(0, 0) == (0.0, 1.0)
    assert wilson(10, 10)[1] == 1.0


def test_collect_report_and_rescore_from_saved_runs(tmp_path):
    results = [
        SearchResult(
            query="김치찌개 레시피",
            title="김치찌개 황금레시피",
            url=f"https://www.10000recipe.com/recipe/{n}",
            content="재료: 돼지고기 200g, 두부 1/2모, 대파 1대",
            fetched_at=FETCHED_AT,
        )
        for n in range(3)
    ]
    agent = WebCollectAgent(StaticSearchProvider({"김치찌개 레시피": results}))
    gold = [GoldMenu("김치찌개", ["돼지고기"], {"is_pork"})]

    runs = asyncio.run(collect_all(agent, ["김치찌개"]))
    path = tmp_path / "raw.json"
    save_runs(runs, path)
    reloaded = load_runs(path)
    report = render_report(gold, reloaded, [score(gold, reloaded, share) for share in (0.0, 0.5)])

    assert len(reloaded["김치찌개"].sources) == 3
    assert score(gold, reloaded, 0.0).total.tp == 1
    assert "레시피를 하나라도 찾은 메뉴: 1/1" in report
    assert "| 레시피 1개 이상 | 100.0%" in report
