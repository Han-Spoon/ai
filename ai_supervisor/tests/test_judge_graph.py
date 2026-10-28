from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest

from ai_db_update import DbUpdateTool
from ai_normalization import CandidateSelectionAgent
from ai_supervisor.contracts.common import NodeWarning
from ai_supervisor.contracts.db_update import CreateReviewItemPayload, DbOperation
from ai_supervisor.contracts.decision import HighRiskReviewResponse
from ai_supervisor.contracts.external import (
    EvidenceRef,
    IngredientGraph,
    ItemResult,
    JudgeRequest,
    PersistenceCommand,
    PrepareSignals,
    PreparedItem,
    OwnerCard,
)
from ai_supervisor.hashing import ContractHashMismatch, prepared_items_hash
from ai_supervisor.judge_graph import (
    ItemAnalysisError,
    JudgeDependencies,
    JudgeGraphInvariantError,
    build_judge_graph,
)
from ai_supervisor.contracts.ontology import VariantSuggestion
from ai_supervisor.contracts.web_search import WebMenu
from ai_supervisor.state import (
    ItemAnalysisOutput,
    ItemReviewCandidate,
    PendingDbOperation,
    RunContext,
)

TRACE_ID = UUID("0199a2f0-0000-7000-8000-000000000001")
PREPARE_ID = UUID("0199a2f0-0000-7000-8000-000000000101")


def prepared_item(index: int) -> PreparedItem:
    return PreparedItem(
        item_id=f"scan-123:{index}",
        source_index=index,
        raw_menu_name=f"메뉴 {index}",
        normalized_menu_name=f"메뉴{index}",
        display_name=f"메뉴 {index}",
        description=None,
        price_text=None,
        price_options=[],
        ocr_confidence=0.95,
        locale_hint="ko",
        options=[],
        residual_tokens=[],
        risk_tag_observations=[],
    )


def empty_ontology() -> dict:
    return {
        "status": "not_found",
        "menu": None,
        "recipe_ingredients": [],
        "menu_recipe_corpora": [],
        "menu_ingredient_priors": [],
        "cache": None,
    }


def request(*, count: int = 3, store_id: int | None = 123) -> JudgeRequest:
    items = [prepared_item(index) for index in range(count)]
    signals = PrepareSignals(scan_quality_status="usable", warnings=[])
    knowledge_items = []
    for index, item in enumerate(items):
        candidate = {
            "menu_id": 1000 + index,
            "menu_category_id": 11,
            "canonical_name": f"메뉴{index}",
            "score": 1.0,
            "match_type": "canonical",
        }
        knowledge_items.append(
            {
                "item_id": item.item_id,
                "candidate_lookup": {
                    "status": "ok",
                    "candidates": [candidate],
                },
                "candidate_knowledge": [
                    {
                        "menu_id": candidate["menu_id"],
                        "exact_feedback": {"status": "not_found", "exact": None},
                        "ontology": empty_ontology(),
                        "priors": {
                            "status": "not_found",
                            "store": [],
                            "evidence": [],
                        },
                    }
                ],
                "fallback_ontology": empty_ontology(),
                "web_search_cache": {
                    "status": "not_found",
                    "cached_sources": [],
                    "next_rank": 1,
                },
            }
        )

    return JudgeRequest.model_validate(
        {
            "schema_version": "3.0",
            "trace_id": str(TRACE_ID),
            "prepare_id": str(PREPARE_ID),
            "scan_session_id": "scan-123",
            "store_id": store_id,
            "prepared_items_hash": prepared_items_hash(items, signals),
            "knowledge_revision": "revision-1",
            "prepare_signals": signals.model_dump(mode="json"),
            "prepared_items": [item.model_dump(mode="json") for item in items],
            "knowledge_bundle": {
                "bundle_schema_version": "3.0",
                "ingredient_graph": {
                    "status": "not_found",
                    "ingredients": [],
                    "aliases": [],
                    "tags": [],
                    "compositions": [],
                    "composition_tags": [],
                },
                "items": knowledge_items,
                "source_reliability": {"status": "not_found", "weights": []},
            },
            "user_profile": {
                "religion_type": None,
                "is_vegetarian": False,
                "vegetarian_type": None,
                "no_alcohol": False,
                "allergies": [],
                "no_spicy": False,
            },
            "locale": "ko",
        }
    )


def context(source: JudgeRequest) -> RunContext:
    return RunContext(
        schema_version=source.schema_version,
        trace_id=source.trace_id,
        scan_session_id=source.scan_session_id,
        store_id=source.store_id,
    )


class DelayedItemAnalyzer:
    def __init__(self, failing_index: int | None = None) -> None:
        self.failing_index = failing_index
        self.completed: list[int] = []
        self.tasks = []

    async def run(self, task, run_context):
        index = task.prepared_item.source_index
        self.tasks.append(task)
        await asyncio.sleep((3 - index) * 0.005)
        if index == self.failing_index:
            raise ItemAnalysisError(
                "item_timeout",
                "메뉴 분석 시간이 초과되었습니다.",
                retryable=True,
            )
        self.completed.append(index)
        selected = task.selection.selected_candidate
        return ItemAnalysisOutput(
            result=ItemResult(
                item_id=task.prepared_item.item_id,
                source_index=index,
                menu_id=selected.menu_id if selected else None,
                menu_category_id=selected.menu_category_id if selected else None,
                normalization_status=task.selection.normalization_status,
                match_type=selected.match_type if selected else None,
                selection_method=task.selection.selection_method,
                status="completed",
                route="db_bayes" if selected else "no_information",
                risk_level="caution",
                evidence_basis="estimated" if selected else "unknown",
                information_status="partial" if selected else "none",
                owner_answer_status="none",
                risk_ingredients=[],
                matched_tags=[],
                evidence_refs=[],
                message={
                    "verdict": "확인이 필요해요.",
                    "reason": None,
                    "notes": [],
                },
                owner_cards=[],
                warnings=list(task.warnings),
                errors=list(task.errors),
            )
        )


class SharedEvidenceAnalyzer(DelayedItemAnalyzer):
    async def run(self, task, run_context):
        output = await super().run(task, run_context)
        evidence = EvidenceRef(
            evidence_id="shared-evidence",
            evidence_class="soft",
            source_type="recipe",
            source_ref="recipe:shared",
            verification_status="approved",
            reliability_weight=1.0,
            observed_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
        )
        command = PersistenceCommand(
            command_id="shared-command",
            operation_id=None,
            item_id=None,
            card_id=None,
            action="save_menu_ingredient_cache",
            idempotency_key="sha256:" + "c" * 64,
            payload={
                "provenance": {
                    "evidence_refs": [evidence.evidence_id],
                }
            },
        )
        return ItemAnalysisOutput(
            result=output.result.model_copy(
                update={"evidence_refs": [evidence.evidence_id]}
            ),
            evidence=(evidence,),
            commands=(command,),
        )


class PendingOperationAnalyzer(DelayedItemAnalyzer):
    async def run(self, task, run_context):
        output = await super().run(task, run_context)
        index = task.prepared_item.source_index
        evidence = EvidenceRef(
            evidence_id=f"variant:{index}",
            evidence_class="soft",
            source_type="ocr_variant_tag",
            source_ref=f"차돌{index}",
            verification_status="pending_review",
            reliability_weight=None,
            observed_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
        )
        operation = DbOperation(
            operation_id=f"operation:{task.prepared_item.item_id}:variant-review",
            operation_type="create_review_item",
            payload=CreateReviewItemPayload(
                review_origin="variant_suggestion",
                menu=WebMenu(
                    menu_id=None,
                    normalized_menu_name=task.prepared_item.normalized_menu_name,
                ),
                web_sources=[],
                review_notes=[],
                variant_suggestion=VariantSuggestion(
                    base_menu_id=1000 + index,
                    remain_token=f"차돌{index}",
                    suggested_ingredients=[],
                    evidence=evidence,
                ),
            ),
        )
        return ItemAnalysisOutput(
            result=output.result,
            evidence=(evidence,),
            operations=(
                PendingDbOperation(
                    item_id=task.prepared_item.item_id,
                    operation=operation,
                ),
            ),
        )


class PendingReviewOperationAnalyzer(PendingOperationAnalyzer):
    async def run(self, task, run_context):
        output = await super().run(task, run_context)
        return ItemAnalysisOutput(
            result=output.result,
            evidence=output.evidence,
            operations=output.operations,
            review=ItemReviewCandidate(
                reason_code="no_information",
                found_ingredients=(),
            ),
        )


class RecordingDbUpdate:
    def __init__(self) -> None:
        self.requests = []
        self.tool = DbUpdateTool()

    async def run(self, request):
        self.requests.append(request)
        return await self.tool.run(request)


class BrokenItemAnalyzer:
    async def run(self, task, run_context):
        raise RuntimeError("programming error")


class UnsafeItemAnalyzer(DelayedItemAnalyzer):
    async def run(self, task, run_context):
        output = await super().run(task, run_context)
        return ItemAnalysisOutput(
            result=output.result.model_copy(update={"risk_level": "safe"})
        )


class UnusedPostAdapter:
    async def run(self, request):
        raise AssertionError("post-processing adapter must not be called")


class ReviewItemAnalyzer(DelayedItemAnalyzer):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__()
        self.reasons = reasons

    async def run(self, task, run_context):
        output = await super().run(task, run_context)
        return ItemAnalysisOutput(
            result=output.result,
            evidence=output.evidence,
            commands=output.commands,
            operations=output.operations,
            review=ItemReviewCandidate(
                reason_code=self.reasons[task.prepared_item.source_index],
                found_ingredients=(),
            ),
        )


class RecordingHighRiskReview:
    def __init__(self, *, add_missed_card: bool = False) -> None:
        self.add_missed_card = add_missed_card
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        missed = []
        cards = list(request.decision.owner_cards)
        if self.add_missed_card:
            choice = request.ingredient_choices[0]
            ingredient = {
                "ingredient_id": choice.ingredient_id,
                "canonical_name": choice.canonical_name,
            }
            missed = [ingredient]
            cards.append(
                OwnerCard(
                    item_id=request.context.item_id,
                    card_id=(
                        f"card:{request.context.item_id}:"
                        f"ingredient-{choice.ingredient_id}"
                    ),
                    menu_id=request.menu.menu_id,
                    ingredient=ingredient,
                    ambiguity_flag=None,
                    constraint_tags=[],
                    question={"ko": f"이 메뉴에 {choice.canonical_name}이 들어가나요?"},
                    reason_codes=["high_risk_review"],
                    evidence_refs=[],
                )
            )
        return HighRiskReviewResponse(
            context=request.context,
            missed_ingredients=missed,
            reason="재검증 결과 추가 확인이 필요해요.",
            owner_cards=cards,
            warnings=request.warnings,
            errors=request.errors,
        )


class TimeoutHighRiskReview:
    async def run(self, request):
        raise TimeoutError("review timeout")


class InvalidHighRiskReview:
    async def run(self, request):
        return HighRiskReviewResponse(
            context=request.context,
            missed_ingredients=[
                {"ingredient_id": 999, "canonical_name": "선택지 밖 재료"}
            ],
            reason="검증되지 않은 재료를 추가했어요.",
            owner_cards=request.decision.owner_cards,
            warnings=request.warnings,
            errors=request.errors,
        )


def with_ingredient_graph(source: JudgeRequest) -> JudgeRequest:
    graph = IngredientGraph.model_validate(
        {
            "status": "ok",
            "ingredients": [
                {
                    "ingredient_id": 130,
                    "canonical_name": "땅콩",
                    "name_normalized": "땅콩",
                    "localized_name": "땅콩",
                    "taxonomy_category": "garnish",
                }
            ],
            "aliases": [],
            "tags": [{"ingredient_id": 130, "tag_code": "is_peanut"}],
            "compositions": [],
            "composition_tags": [],
        }
    )
    return source.model_copy(
        update={
            "knowledge_bundle": source.knowledge_bundle.model_copy(
                update={"ingredient_graph": graph}
            )
        }
    )


def graph(analyzer, *, reviewer=None, db_update=None):
    return build_judge_graph(
        JudgeDependencies(
            candidate_selection=CandidateSelectionAgent(),
            item_analyzer=analyzer,
            high_risk_review=reviewer or UnusedPostAdapter(),
            db_update=db_update or UnusedPostAdapter(),
        )
    )


def test_judge_graph_fans_out_concurrently_and_collects_in_source_order() -> None:
    source = request()
    analyzer = DelayedItemAnalyzer()

    result = asyncio.run(
        graph(analyzer).ainvoke({"request": source}, context=context(source))
    )

    assert analyzer.completed == [2, 1, 0]
    assert [item.source_index for item in result["ordered_items"]] == [0, 1, 2]
    assert [task.knowledge.selected.menu_id for task in analyzer.tasks] == [
        1000,
        1001,
        1002,
    ]
    assert len(result["item_updates"]) == 3


def test_judge_graph_degrades_only_expected_item_failure() -> None:
    source = request()
    analyzer = DelayedItemAnalyzer(failing_index=1)

    result = asyncio.run(
        graph(analyzer).ainvoke({"request": source}, context=context(source))
    )

    items = result["ordered_items"]
    assert [item.status for item in items] == [
        "completed",
        "partial_failed",
        "completed",
    ]
    assert items[1].risk_level == "caution"
    assert items[1].errors[0].code == "item_timeout"


def test_storeless_judge_passes_explicit_null_and_safety_warning() -> None:
    source = request(count=1, store_id=None)
    analyzer = ReviewItemAnalyzer(["no_information"])

    result = asyncio.run(
        graph(analyzer).ainvoke({"request": source}, context=context(source))
    )

    task = analyzer.tasks[0]
    item = result["ordered_items"][0]
    assert any(warning.code == "store_context_missing" for warning in task.warnings)
    assert any(warning.code == "store_context_missing" for warning in item.warnings)
    assert item.risk_level == "caution"
    assert result["persistence_commands"] == []


def test_judge_preserves_scan_level_prepare_warning_without_item_assignment() -> None:
    source = request(count=1)
    signals = PrepareSignals(
        scan_quality_status="low_confidence",
        warnings=[
            NodeWarning(
                code="scan_quality_low_confidence",
                node="ocr",
                item_id=None,
                message="사진 품질이 낮습니다.",
                forces_caution=True,
            )
        ],
    )
    source = source.model_copy(
        update={
            "prepare_signals": signals,
            "prepared_items_hash": prepared_items_hash(
                source.prepared_items, signals
            ),
        }
    )

    result = asyncio.run(
        graph(DelayedItemAnalyzer()).ainvoke(
            {"request": source}, context=context(source)
        )
    )

    assert [warning.code for warning in result["warnings"]] == [
        "scan_quality_low_confidence"
    ]
    assert result["ordered_items"][0].warnings == []


def test_judge_rejects_safe_result_when_item_safety_warning_is_active() -> None:
    source = request(count=1)
    signals = PrepareSignals(
        scan_quality_status="usable",
        warnings=[
            NodeWarning(
                code="low_ocr_confidence",
                node="normalization",
                item_id="scan-123:0",
                message="메뉴 글자 인식 신뢰도가 낮습니다.",
                forces_caution=True,
            )
        ],
    )
    source = source.model_copy(
        update={
            "prepare_signals": signals,
            "prepared_items_hash": prepared_items_hash(
                source.prepared_items, signals
            ),
        }
    )

    with pytest.raises(JudgeGraphInvariantError, match="cannot be safe"):
        asyncio.run(
            graph(UnsafeItemAnalyzer()).ainvoke(
                {"request": source}, context=context(source)
            )
        )


def test_collect_deduplicates_shared_evidence_and_commands() -> None:
    source = request(count=2)

    result = asyncio.run(
        graph(SharedEvidenceAnalyzer()).ainvoke(
            {"request": source}, context=context(source)
        )
    )

    assert [item.evidence_id for item in result["evidence"]] == [
        "shared-evidence"
    ]
    assert [command.command_id for command in result["persistence_commands"]] == [
        "shared-command"
    ]


def test_judge_calls_db_update_once_for_all_item_operations() -> None:
    source = request(count=2)
    db_update = RecordingDbUpdate()

    result = asyncio.run(
        graph(PendingOperationAnalyzer(), db_update=db_update).ainvoke(
            {"request": source}, context=context(source)
        )
    )

    assert len(db_update.requests) == 1
    request_value = db_update.requests[0]
    assert request_value.context.call_scope == "out_of_band"
    assert request_value.context.item_id is None
    assert len(request_value.operations) == 2
    assert [
        command.item_id for command in result["response"].persistence_commands
    ] == ["scan-123:0", "scan-123:1"]


def test_judge_combines_item_and_owner_operations_in_one_db_update_call() -> None:
    source = with_ingredient_graph(request(count=1))
    db_update = RecordingDbUpdate()

    result = asyncio.run(
        graph(
            PendingReviewOperationAnalyzer(),
            reviewer=RecordingHighRiskReview(add_missed_card=True),
            db_update=db_update,
        ).ainvoke({"request": source}, context=context(source))
    )

    assert len(db_update.requests) == 1
    assert [
        operation.operation_type for operation in db_update.requests[0].operations
    ] == ["create_review_item", "create_owner_verification_request"]
    assert [
        command.action for command in result["response"].persistence_commands
    ] == ["create_review_item", "create_owner_verification_request"]


def test_judge_graph_rejects_changed_prepare_hash_before_downstream_calls() -> None:
    source = request(count=1)
    changed = source.model_copy(
        update={"prepared_items_hash": "sha256:" + "0" * 64}
    )
    analyzer = DelayedItemAnalyzer()

    with pytest.raises(ContractHashMismatch):
        asyncio.run(
            graph(analyzer).ainvoke({"request": changed}, context=context(changed))
        )
    assert analyzer.tasks == []


def test_judge_graph_does_not_hide_unexpected_item_errors() -> None:
    source = request(count=1)

    with pytest.raises(RuntimeError, match="programming error"):
        asyncio.run(
            graph(BrokenItemAnalyzer()).ainvoke(
                {"request": source}, context=context(source)
            )
        )


def test_judge_graph_compiles_with_documented_phase_four_nodes() -> None:
    nodes = graph(DelayedItemAnalyzer()).get_graph().nodes
    assert {
        "validate_knowledge",
        "select_candidates",
        "analyze_item",
        "collect_items",
        "high_risk_review",
        "db_update",
        "finalize",
    } <= set(nodes)


def test_high_risk_review_adds_card_then_db_update_builds_command() -> None:
    source = with_ingredient_graph(request(count=1))
    reviewer = RecordingHighRiskReview(add_missed_card=True)

    result = asyncio.run(
        graph(
            ReviewItemAnalyzer(["no_information"]),
            reviewer=reviewer,
            db_update=DbUpdateTool(),
        ).ainvoke({"request": source}, context=context(source))
    )

    response = result["response"]
    item = response.items[0]
    assert item.risk_level == "caution"
    assert item.message.reason == "재검증 결과 추가 확인이 필요해요."
    assert [card.card_id for card in item.owner_cards] == [
        "card:scan-123:0:ingredient-130"
    ]
    assert [command.action for command in response.persistence_commands] == [
        "create_owner_verification_request"
    ]
    assert response.persistence_commands[0].card_id == item.owner_cards[0].card_id


def test_high_risk_review_selects_at_most_three_by_reason_priority() -> None:
    source = request(count=5)
    reviewer = RecordingHighRiskReview()
    reasons = [
        "insufficient_web_sources",
        "partial_information",
        "anomaly",
        "no_information",
        "unknown_tag",
    ]

    asyncio.run(
        graph(
            ReviewItemAnalyzer(reasons),
            reviewer=reviewer,
            db_update=UnusedPostAdapter(),
        ).ainvoke({"request": source}, context=context(source))
    )

    assert [request.reason_code for request in reviewer.requests] == [
        "anomaly",
        "unknown_tag",
        "no_information",
    ]


def test_high_risk_timeout_keeps_original_item_result() -> None:
    source = request(count=1)

    result = asyncio.run(
        graph(
            ReviewItemAnalyzer(["partial_information"]),
            reviewer=TimeoutHighRiskReview(),
            db_update=UnusedPostAdapter(),
        ).ainvoke({"request": source}, context=context(source))
    )

    item = result["response"].items[0]
    assert item.message.reason is None
    assert item.owner_cards == []


def test_invalid_high_risk_output_keeps_original_item_result() -> None:
    source = with_ingredient_graph(request(count=1))

    result = asyncio.run(
        graph(
            ReviewItemAnalyzer(["unknown_tag"]),
            reviewer=InvalidHighRiskReview(),
            db_update=UnusedPostAdapter(),
        ).ainvoke({"request": source}, context=context(source))
    )

    item = result["response"].items[0]
    assert item.message.reason is None
    assert item.owner_cards == []
