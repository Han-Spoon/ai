"""The real ⑦ adapter (with real ④⑤) inside the Supervisor item graph."""

import asyncio

from ai_bayesian import BayesianTool
from ai_db_update import DbUpdateTool
from ai_ontology import IngredientMappingTool, OntologyTool, TagLookupTool
from ai_supervisor.item_graph import ItemGraphAnalyzer, ItemGraphDependencies
from ai_supervisor.tests.test_item_graph import NOW, RuleDecision, UnusedAdapter, context
from ai_supervisor.tests.test_item_graph_web import (
    RecordingAggregate,
    RecordingCollect,
    unknown_task,
    web_source,
)


def test_web_path_builds_real_review_and_cache_commands():
    analyzer = ItemGraphAnalyzer(
        ItemGraphDependencies(
            ontology=OntologyTool(),
            bayesian=BayesianTool(),
            tag_lookup=TagLookupTool(),
            decision=RuleDecision(),
            web_collect=RecordingCollect([web_source()]),
            ingredient_mapping=IngredientMappingTool(),
            web_aggregate=RecordingAggregate(),
            db_update=DbUpdateTool(),
            now_factory=lambda: NOW,
        )
    )
    output = asyncio.run(analyzer.run(unknown_task(), context()))
    assert output.result.route == "web_bayes"
    assert [c.action for c in output.commands] == ["create_review_item", "save_web_search_cache"]
    assert all(c.idempotency_key.startswith("sha256:") for c in output.commands)


def test_runtime_variant_review_with_real_tools():
    from dataclasses import replace

    from ai_supervisor.tests.test_item_graph import runtime_variant_task

    task = runtime_variant_task()
    prepared = task.prepared_item.model_copy(update={"normalized_menu_name": "소고기김치찌개"})
    analyzer = ItemGraphAnalyzer(
        ItemGraphDependencies(
            ontology=OntologyTool(),
            bayesian=BayesianTool(),
            tag_lookup=TagLookupTool(),
            decision=RuleDecision(),
            web_collect=UnusedAdapter(),
            ingredient_mapping=UnusedAdapter(),
            web_aggregate=UnusedAdapter(),
            db_update=DbUpdateTool(),
            now_factory=lambda: NOW,
        )
    )
    output = asyncio.run(analyzer.run(replace(task, prepared_item=prepared), context()))
    assert output.result.route == "db_variant_bayes"
    review = [c for c in output.commands if c.action == "create_review_item"]
    assert review and review[0].payload["provenance"]["source_type"] == "ocr_variant_tag"
