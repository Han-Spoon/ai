"""The real ④ adapters inside the Supervisor item graph (⓪ §2-8)."""

import asyncio

from ai_ontology import OntologyTool, TagLookupTool
from ai_supervisor.item_graph import ItemGraphAnalyzer, ItemGraphDependencies
from ai_supervisor.tests.test_item_graph import (
    NOW,
    RecordingBayesian,
    RuleDecision,
    UnusedAdapter,
    context,
    item_task,
)


def run(store_id=123):
    bayesian = RecordingBayesian()
    analyzer = ItemGraphAnalyzer(
        ItemGraphDependencies(
            ontology=OntologyTool(),
            bayesian=bayesian,
            tag_lookup=TagLookupTool(),
            decision=RuleDecision(),
            web_collect=UnusedAdapter(),
            ingredient_mapping=UnusedAdapter(),
            web_aggregate=UnusedAdapter(),
            db_update=UnusedAdapter(),
            now_factory=lambda: NOW,
        )
    )
    output = asyncio.run(analyzer.run(item_task(exact_complete=False), context(store_id)))
    return output, bayesian


def test_db_path_runs_with_real_ontology():
    output, bayesian = run()
    assert output.result.route == "db_bayes"
    sent = [i.ingredient.ingredient_id for i in bayesian.requests[0].ingredients]
    assert sent == [102]  # confirmed 돼지고기 is not sent again
    assert [c.action for c in output.commands] == ["save_menu_ingredient_cache"]


def test_storeless_path_has_no_cache_command():
    output, _ = run(store_id=None)
    assert not output.commands
    assert output.result.risk_level != "safe"
