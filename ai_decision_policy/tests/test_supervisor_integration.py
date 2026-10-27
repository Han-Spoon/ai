"""The real ⑧ adapter (with the real ④⑤) inside the Supervisor item graph (⓪ §4-7)."""

import asyncio

from ai_bayesian import BayesianTool
from ai_decision_policy import DecisionTool
from ai_ontology import OntologyTool, TagLookupTool
from ai_supervisor.item_graph import ItemGraphAnalyzer, ItemGraphDependencies
from ai_supervisor.tests.test_item_graph import NOW, UnusedAdapter, context, item_task


def run(*, exact_complete: bool, store_id: int | None = 123):
    analyzer = ItemGraphAnalyzer(
        ItemGraphDependencies(
            ontology=OntologyTool(),
            bayesian=BayesianTool(),
            tag_lookup=TagLookupTool(),
            decision=DecisionTool(),
            web_collect=UnusedAdapter(),
            ingredient_mapping=UnusedAdapter(),
            web_aggregate=UnusedAdapter(),
            db_update=UnusedAdapter(),
            now_factory=lambda: NOW,
        )
    )
    return asyncio.run(analyzer.run(item_task(exact_complete=exact_complete), context(store_id)))


def test_owner_confirmed_pork_reaches_the_result_as_danger():
    result = run(exact_complete=True).result
    assert (result.risk_level, result.evidence_basis) == ("danger", "confirmed")
    assert result.message.verdict == "돼지고기가 들어 있어요."
    assert result.message.reason is None


def test_db_path_with_real_ontology_bayesian_and_decision():
    result = run(exact_complete=False).result
    assert result.route == "db_bayes"
    assert result.risk_level == "danger"
    assert result.message.verdict == "돼지고기가 들어 있어요."


def test_storeless_scan_is_caution_without_owner_cards():
    result = run(exact_complete=False, store_id=None).result
    assert result.risk_level == "caution"
    assert result.owner_cards == []
    assert result.message.verdict
