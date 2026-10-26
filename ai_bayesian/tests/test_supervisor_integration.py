"""The real ⑤ adapter (with the real ④) inside the Supervisor item graph."""

import asyncio

from ai_bayesian import BayesianTool
from ai_ontology import OntologyTool, TagLookupTool
from ai_supervisor.item_graph import ItemGraphAnalyzer, ItemGraphDependencies
from ai_supervisor.tests.test_item_graph import NOW, RuleDecision, UnusedAdapter, context, item_task


def test_db_path_runs_with_real_ontology_and_bayesian():
    decision = RuleDecision()
    analyzer = ItemGraphAnalyzer(
        ItemGraphDependencies(
            ontology=OntologyTool(),
            bayesian=BayesianTool(),
            tag_lookup=TagLookupTool(),
            decision=decision,
            web_collect=UnusedAdapter(),
            ingredient_mapping=UnusedAdapter(),
            web_aggregate=UnusedAdapter(),
            db_update=UnusedAdapter(),
            now_factory=lambda: NOW,
        )
    )
    output = asyncio.run(analyzer.run(item_task(exact_complete=False), context()))
    assert output.result.route == "db_bayes"
    probability = decision.requests[0].probability_results[0]
    assert probability.prior_source == "global"
    assert 0 < probability.posterior_mean < 1
