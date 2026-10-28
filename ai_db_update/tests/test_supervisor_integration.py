"""Item Graph operations materialized by the real ⑦ adapter."""

import asyncio

from ai_bayesian import BayesianTool
from ai_db_update import DbUpdateTool
from ai_ontology import IngredientMappingTool, OntologyTool, TagLookupTool
from ai_supervisor.contracts.db_update import DbUpdateRequest
from ai_supervisor.item_graph import ItemGraphAnalyzer, ItemGraphDependencies
from ai_supervisor.tests.test_item_graph import NOW, RuleDecision, UnusedAdapter, context
from ai_supervisor.tests.test_item_graph_web import (
    RecordingAggregate,
    RecordingCollect,
    unknown_task,
    web_source,
)


def materialize_operations(output):
    run_context = context()
    request = DbUpdateRequest(
        context=run_context.node_context(
            "out_of_band", None, include_store=True
        ),
        operations=[pending.operation for pending in output.operations],
        warnings=[],
        errors=[],
    )
    return asyncio.run(DbUpdateTool().run(request))


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
            now_factory=lambda: NOW,
        )
    )
    output = asyncio.run(analyzer.run(unknown_task(), context()))
    assert output.result.route == "web_bayes"
    assert output.commands == ()
    assert [
        pending.operation.operation_type for pending in output.operations
    ] == ["create_review_item", "save_web_search_cache"]

    response = materialize_operations(output)
    assert [command.action for command in response.commands] == [
        "create_review_item",
        "save_web_search_cache",
    ]
    assert all(
        command.idempotency_key.startswith("sha256:")
        for command in response.commands
    )


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
            now_factory=lambda: NOW,
        )
    )
    output = asyncio.run(analyzer.run(replace(task, prepared_item=prepared), context()))
    assert output.result.route == "db_variant_bayes"
    assert output.commands == ()
    assert [
        pending.operation.operation_type for pending in output.operations
    ] == ["create_review_item"]

    response = materialize_operations(output)
    review = [
        command
        for command in response.commands
        if command.action == "create_review_item"
    ]
    assert review
    assert review[0].payload.provenance.source_type == "ocr_variant_tag"
