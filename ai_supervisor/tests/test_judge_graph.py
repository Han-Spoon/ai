from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest

from ai_normalization import CandidateSelectionAgent
from ai_supervisor.contracts.common import NodeWarning
from ai_supervisor.contracts.external import (
    EvidenceRef,
    ItemResult,
    JudgeRequest,
    PersistenceCommand,
    PrepareSignals,
    PreparedItem,
)
from ai_supervisor.hashing import ContractHashMismatch, prepared_items_hash
from ai_supervisor.judge_graph import (
    ItemAnalysisError,
    JudgeDependencies,
    JudgeGraphInvariantError,
    build_judge_graph,
)
from ai_supervisor.state import ItemAnalysisOutput, RunContext

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


class BrokenItemAnalyzer:
    async def run(self, task, run_context):
        raise RuntimeError("programming error")


class UnsafeItemAnalyzer(DelayedItemAnalyzer):
    async def run(self, task, run_context):
        output = await super().run(task, run_context)
        return ItemAnalysisOutput(
            result=output.result.model_copy(update={"risk_level": "safe"})
        )


def graph(analyzer):
    return build_judge_graph(
        JudgeDependencies(
            candidate_selection=CandidateSelectionAgent(),
            item_analyzer=analyzer,
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
    analyzer = DelayedItemAnalyzer()

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
    } <= set(nodes)
