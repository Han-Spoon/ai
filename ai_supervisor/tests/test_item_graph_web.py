"""Item Graph web path from ④ unknown through ⑥, ⑦, ⑤, and ⑧."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from hashlib import sha256

import pytest

from ai_normalization.contracts import CandidateSelectionResult
from ai_supervisor.contracts.bayesian import BayesianResponse, ProbabilityResult
from ai_supervisor.contracts.db_update import DbUpdateResponse
from ai_supervisor.contracts.external import OntologySlice, WebSearchCache
from ai_supervisor.contracts.ontology import (
    IngredientMappingResponse,
    IngredientMappingResult,
    OntologyResponse,
    OntologyResultMenu,
)
from ai_supervisor.contracts.web_search import WebAggregateResponse, WebCollectResponse
from ai_supervisor.item_graph import (
    ItemGraphAnalyzer,
    ItemGraphDependencies,
    ItemGraphInvariantError,
)
from ai_supervisor.tests.test_item_graph import (
    NOW,
    RecordingTagLookup,
    RuleDecision,
    context,
    item_task,
)


def web_source(index: int = 1) -> dict:
    url = f"https://www.10000recipe.com/recipe/{index}"
    return {
        "source_url": url,
        "title": "마라탕 레시피",
        "fetched_at": "2026-10-23T00:00:00Z",
        "evidence": {
            "evidence_id": f"web:{url}",
            "evidence_class": "soft",
            "source_type": "web_search",
            "source_ref": url,
            "verification_status": "pending_review",
            "reliability_weight": None,
            "observed_at": "2026-10-23T00:00:00Z",
        },
        "ingredients": [
            {"ingredient_id": None, "canonical_name": "소고기"}
        ],
    }


def unknown_task(*, cached_sources: list[dict] | None = None):
    task = item_task(exact_complete=False)
    prepared = task.prepared_item.model_copy(
        update={
            "raw_menu_name": "마라탕",
            "normalized_menu_name": "마라탕",
            "display_name": "마라탕",
        }
    )
    selection = CandidateSelectionResult.model_validate(
        {
            **prepared.model_dump(mode="json"),
            "candidate_status": "not_found",
            "match_candidates": [],
            "normalization_status": "unmatched",
            "selection_method": "none",
            "selection_reason_codes": ["candidate_not_found"],
            "selected_candidate": None,
        }
    )
    sources = cached_sources or []
    cache = WebSearchCache.model_validate(
        {
            "status": "ok" if sources else "not_found",
            "cached_sources": sources,
        }
    )
    return replace(
        task,
        prepared_item=prepared,
        selection=selection,
        knowledge=replace(
            task.knowledge,
            selected=None,
            fallback_ontology=OntologySlice.model_validate(
                {
                    "status": "not_found",
                    "menu": None,
                    "recipe_ingredients": [],
                    "menu_recipe_corpora": [],
                    "menu_ingredient_priors": [],
                    "cache": None,
                }
            ),
            web_search_cache=cache,
        ),
    )


class UnknownOntology:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return OntologyResponse(
            context=request.context,
            menu=OntologyResultMenu(
                menu_id=None,
                base_menu_id=None,
                remain_token=None,
                menu_category_id=None,
                menu_category=None,
                exists_in_db=False,
                is_variant=False,
                variant_origin=None,
            ),
            menu_ambiguity_flags=[],
            ingredients=[],
            evidence=[],
            variant_suggestion=None,
            unmapped_tokens=[],
            cache_command=None,
            warnings=request.warnings,
            errors=request.errors,
        )


class RecordingCollect:
    def __init__(self, sources: list[dict]) -> None:
        self.sources = sources
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return WebCollectResponse.model_validate(
            {
                "context": request.context.model_dump(mode="json"),
                "menu": {
                    "menu_id": None,
                    "normalized_menu_name": request.menu.normalized_menu_name,
                },
                "found": bool(self.sources),
                "sources": self.sources,
                "warnings": [item.model_dump(mode="json") for item in request.warnings],
                "errors": [item.model_dump(mode="json") for item in request.errors],
            }
        )


class ConcurrencyCollect(RecordingCollect):
    def __init__(self, sources: list[dict]) -> None:
        super().__init__(sources)
        self.active = 0
        self.max_active = 0

    async def run(self, request):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return await super().run(request)
        finally:
            self.active -= 1


class RecordingMapper:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return IngredientMappingResponse(
            context=request.context,
            results=[
                IngredientMappingResult(
                    input_name=name,
                    ingredient={"ingredient_id": 120, "canonical_name": "소고기"},
                    constraint_tags=["is_beef"],
                    role="identity",
                )
                for name in request.ingredient_names
            ],
            warnings=request.warnings,
            errors=request.errors,
        )


class WebBayesian:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        assert request.priors.status == "not_found"
        assert request.priors.store == []
        return BayesianResponse(
            context=request.context,
            probabilities=[
                ProbabilityResult(
                    ingredient=item.ingredient,
                    constraint_tags=item.constraint_tags,
                    role=item.role,
                    posterior_mean=0.5,
                    alpha_post=2.0,
                    beta_post=2.0,
                    prior_source="uninformative",
                    source=item.source,
                    depth=item.depth,
                    confidence=0.5,
                    anomaly_locked=item.anomaly_locked,
                    evidence_refs=item.evidence_refs,
                )
                for item in request.ingredients
            ],
            warnings=request.warnings,
            errors=request.errors,
        )


class RecordingAggregate:
    def __init__(self, recipe_signals: list[dict] | None = None) -> None:
        self.requests = []
        self.recipe_signals = recipe_signals

    async def run(self, request):
        self.requests.append(request)
        recipe_signals = self.recipe_signals
        if recipe_signals is None:
            new_sources = [
                source
                for source in request.mapped_sources
                if source.source_origin == "new"
            ]
            recipe_signals = (
                [
                    {
                        "evidence_id": new_sources[0].evidence_id,
                        "code": "title_mismatch",
                        "message": "검토가 필요한 테스트 레시피예요.",
                    }
                ]
                if new_sources
                else []
            )
        mapped = [
            item
            for source in request.mapped_sources
            for item in source.ingredients
        ]
        evidence_refs = list(
            dict.fromkeys(source.evidence_id for source in request.mapped_sources)
        )
        return WebAggregateResponse.model_validate(
            {
                "context": request.context.model_dump(mode="json"),
                "menu": request.menu.model_dump(mode="json"),
                "found": bool(mapped),
                "ingredients": (
                    [
                        {
                            "ingredient": mapped[0].ingredient.model_dump(mode="json"),
                            "constraint_tags": mapped[0].constraint_tags,
                            "role": mapped[0].role,
                            "source": "web_search",
                            "depth": 0,
                            "curated": False,
                            "observation_basis": "direct",
                            "observations": [
                                {
                                    "corpus": request.mapped_sources[0].corpus,
                                    "k_count": len(request.mapped_sources),
                                    "n_total": len(request.mapped_sources),
                                }
                            ],
                            "anomaly_locked": False,
                            "evidence_refs": evidence_refs,
                        }
                    ]
                    if mapped
                    else []
                ),
                "review_notes": [],
                "recipe_signals": recipe_signals,
                "warnings": [item.model_dump(mode="json") for item in request.warnings],
                "errors": [item.model_dump(mode="json") for item in request.errors],
            }
        )


class LegacyAggregate(RecordingAggregate):
    async def run(self, request):
        response = await super().run(request)
        payload = response.model_dump(mode="json")
        payload.pop("recipe_signals")
        return WebAggregateResponse.model_validate(payload)


class RecordingDbUpdate:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        evidence_refs = [
            source.evidence.evidence_id
            for operation in request.operations
            if operation.operation_type == "create_review_item"
            for source in operation.payload.web_sources
        ]
        commands = []
        for index, operation in enumerate(request.operations):
            payload = operation.payload.model_dump(mode="json")
            payload["provenance"] = {
                "origin_node": "web_search",
                "source_type": "web_search",
                "evidence_class": "soft",
                "evidence_refs": evidence_refs,
            }
            commands.append(
                {
                    "command_id": f"command-{index}",
                    "operation_id": operation.operation_id,
                    "item_id": request.context.item_id,
                    "card_id": None,
                    "action": operation.operation_type,
                    "idempotency_key": "sha256:"
                    + sha256(operation.operation_id.encode()).hexdigest(),
                    "payload": payload,
                }
            )
        return DbUpdateResponse.model_validate(
            {
                "context": request.context.model_dump(mode="json"),
                "commands": commands,
                "warnings": [item.model_dump(mode="json") for item in request.warnings],
                "errors": [item.model_dump(mode="json") for item in request.errors],
            }
        )


def analyzer(
    sources: list[dict],
    *,
    collect_adapter=None,
    aggregate_adapter=None,
    max_concurrent_web_searches: int = 5,
):
    ontology = UnknownOntology()
    collect = collect_adapter or RecordingCollect(sources)
    mapper = RecordingMapper()
    bayesian = WebBayesian()
    tags = RecordingTagLookup()
    decision = RuleDecision()
    db_update = RecordingDbUpdate()
    item_analyzer = ItemGraphAnalyzer(
        ItemGraphDependencies(
            ontology=ontology,
            bayesian=bayesian,
            tag_lookup=tags,
            decision=decision,
            web_collect=collect,
            ingredient_mapping=mapper,
            web_aggregate=aggregate_adapter or RecordingAggregate(),
            db_update=db_update,
            max_concurrent_web_searches=max_concurrent_web_searches,
            now_factory=lambda: NOW,
        )
    )
    return item_analyzer, collect, mapper, bayesian, tags, decision, db_update


def task_at_index(index: int):
    task = unknown_task()
    item_id = f"scan-123:{index}"
    prepared = task.prepared_item.model_copy(
        update={"item_id": item_id, "source_index": index}
    )
    selection = task.selection.model_copy(
        update={"item_id": item_id, "source_index": index}
    )
    return replace(
        task,
        prepared_item=prepared,
        selection=selection,
        knowledge=replace(task.knowledge, item_id=item_id),
    )


def test_web_path_maps_aggregates_reviews_and_calculates() -> None:
    item_analyzer, collect, mapper, bayesian, tags, decision, db_update = analyzer(
        [web_source()]
    )

    output = asyncio.run(item_analyzer.run(unknown_task(), context()))

    assert output.result.route == "web_bayes"
    assert output.result.risk_level == "caution"
    assert collect.requests[0].max_sources == 10
    assert mapper.requests[0].ingredient_names == ["소고기"]
    assert bayesian.requests[0].ingredients[0].constraint_tags == ["is_beef"]
    assert tags.requests == []
    assert decision.requests[0].probability_results[0].prior_source == "uninformative"
    assert output.commands == ()
    assert [item.operation.operation_type for item in output.operations] == [
        "create_review_item",
        "save_web_search_cache",
    ]
    assert db_update.requests == []
    assert [item.evidence_id for item in output.evidence] == [
        "web:https://www.10000recipe.com/recipe/1"
    ]


def test_sources_without_recipe_signals_are_cached_without_review() -> None:
    item_analyzer, *_ = analyzer(
        [web_source()], aggregate_adapter=RecordingAggregate([])
    )

    output = asyncio.run(item_analyzer.run(unknown_task(), context()))

    assert [item.operation.operation_type for item in output.operations] == [
        "save_web_search_cache"
    ]
    assert len(output.operations[0].operation.payload.sources) == 1


def test_legacy_aggregate_without_recipe_signals_keeps_review_all_fallback() -> None:
    item_analyzer, *_ = analyzer(
        [web_source()], aggregate_adapter=LegacyAggregate([])
    )

    output = asyncio.run(item_analyzer.run(unknown_task(), context()))

    assert [item.operation.operation_type for item in output.operations] == [
        "create_review_item",
        "save_web_search_cache",
    ]


def test_only_signaled_recipes_are_reviewed_and_all_sources_are_cached() -> None:
    sources = [web_source(1), web_source(2)]
    signaled_id = sources[1]["evidence"]["evidence_id"]
    aggregate = RecordingAggregate(
        [
            {
                "evidence_id": signaled_id,
                "code": "title_mismatch",
                "message": "제목에 메뉴명이 없어요.",
            }
        ]
    )
    item_analyzer, *_ = analyzer(sources, aggregate_adapter=aggregate)

    output = asyncio.run(item_analyzer.run(unknown_task(), context()))

    assert [item.operation.operation_type for item in output.operations] == [
        "create_review_item",
        "save_web_search_cache",
    ]
    review_payload = output.operations[0].operation.payload
    cache_payload = output.operations[1].operation.payload
    assert [
        source.evidence.evidence_id for source in review_payload.web_sources
    ] == [signaled_id]
    assert [
        source.evidence.evidence_id for source in cache_payload.sources
    ] == [source["evidence"]["evidence_id"] for source in sources]


def test_recipe_signal_must_reference_a_new_web_source() -> None:
    aggregate = RecordingAggregate(
        [
            {
                "evidence_id": "web:https://invalid.test/recipe/1",
                "code": "unmapped_ingredient",
                "message": "표준 재료로 맞추지 못했어요.",
            }
        ]
    )
    item_analyzer, *_ = analyzer(
        [web_source()], aggregate_adapter=aggregate
    )

    with pytest.raises(
        ItemGraphInvariantError,
        match="recipe signals must resolve to new web sources",
    ):
        asyncio.run(item_analyzer.run(unknown_task(), context()))


def test_cached_sources_continue_without_new_storage_commands() -> None:
    item_analyzer, collect, mapper, bayesian, _, _, db_update = analyzer([])

    output = asyncio.run(
        item_analyzer.run(
            unknown_task(cached_sources=[web_source(10)]),
            context(),
        )
    )

    assert len(collect.requests[0].cached_sources) == 1
    assert mapper.requests[0].ingredient_names == ["소고기"]
    assert bayesian.requests
    assert db_update.requests == []
    assert output.result.route == "web_bayes"
    assert output.commands == ()
    assert output.operations == ()


def test_web_no_information_skips_mapping_and_bayesian() -> None:
    item_analyzer, _, mapper, bayesian, _, decision, db_update = analyzer([])

    output = asyncio.run(item_analyzer.run(unknown_task(), context()))

    assert mapper.requests == []
    assert bayesian.requests == []
    assert db_update.requests == []
    assert decision.requests[0].no_information is True
    assert decision.requests[0].information_status == "none"
    assert output.result.route == "no_information"
    assert output.result.risk_level == "caution"


def test_storeless_web_path_analyzes_without_commands() -> None:
    item_analyzer, _, _, bayesian, _, decision, db_update = analyzer([web_source()])

    output = asyncio.run(
        item_analyzer.run(unknown_task(), context(store_id=None))
    )

    assert bayesian.requests
    assert db_update.requests == []
    assert decision.requests[0].owner_interaction_allowed is False
    assert output.result.risk_level == "caution"
    assert output.commands == ()
    assert output.operations == ()
    assert any(warning.code == "store_context_missing" for warning in output.result.warnings)


def test_web_collection_concurrency_is_capped_at_five() -> None:
    collect = ConcurrencyCollect([web_source()])
    item_analyzer, *_ = analyzer([], collect_adapter=collect)

    async def run_all():
        request_analyzer = item_analyzer.for_request()
        run_context = context()
        return await asyncio.gather(
            *(
                request_analyzer.run(task_at_index(index), run_context)
                for index in range(7)
            )
        )

    outputs = asyncio.run(run_all())

    assert len(outputs) == 7
    assert collect.max_active == 5


def test_judge_requests_share_five_service_web_search_slots() -> None:
    collect = ConcurrencyCollect([web_source()])
    item_analyzer, *_ = analyzer([], collect_adapter=collect)

    async def run_two_requests():
        first = item_analyzer.for_request()
        second = item_analyzer.for_request()
        assert first.web_search_slots is second.web_search_slots
        first_context = context()
        second_context = context()
        return await asyncio.gather(
            *(
                first.run(task_at_index(index), first_context)
                for index in range(7)
            ),
            *(
                second.run(task_at_index(index), second_context)
                for index in range(7)
            ),
        )

    outputs = asyncio.run(run_two_requests())

    assert len(outputs) == 14
    assert collect.max_active == 5
