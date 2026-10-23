from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

import pytest

from ai_decision_policy import decide
from ai_normalization.contracts import CandidateSelectionResult
from ai_supervisor.contracts.bayesian import BayesianResponse, ProbabilityResult
from ai_supervisor.contracts.decision import DecisionResponse
from ai_supervisor.contracts.external import (
    CandidateKnowledge,
    ExactFeedbackSlice,
    IngredientGraph,
    PreparedItem,
    SourceReliability,
    UserProfile,
)
from ai_supervisor.contracts.ontology import (
    OntologyIngredient,
    OntologyResponse,
    OntologyResultMenu,
    TagLookupResponse,
    TagLookupResult,
)
from ai_supervisor.item_graph import (
    ItemGraphAnalyzer,
    ItemGraphDependencies,
    ItemGraphInvariantError,
)
from ai_supervisor.judge_graph import ItemAnalysisError
from ai_supervisor.state import ItemTask, ItemTaskKnowledge, RunContext

TRACE_ID = UUID("0199a2f0-0000-7000-8000-000000000001")
NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def evidence(evidence_id: str, source_type: str = "recipe") -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_class": "hard" if source_type == "owner_feedback" else "soft",
        "source_type": source_type,
        "source_ref": f"source:{evidence_id}",
        "verification_status": (
            "owner_confirmed" if source_type == "owner_feedback" else "approved"
        ),
        "reliability_weight": 1.0,
        "observed_at": "2026-10-01T00:00:00Z",
    }


def ontology_slice(*, cache: bool) -> dict:
    return {
        "status": "ok",
        "menu": {
            "menu_id": 1001,
            "base_menu_id": None,
            "remain_token": None,
            "menu_category_id": 11,
            "source": "curated",
            "ambiguity_flags": [],
            "knowledge_revision": 7,
        },
        "recipe_ingredients": [{"ingredient_id": 102}],
        "menu_recipe_corpora": [{"corpus": "recipes", "n_total": 10}],
        "menu_ingredient_priors": [
            {"corpus": "recipes", "ingredient_id": 102, "k_count": 4}
        ],
        "cache": (
            {
                "menu_revision": 7,
                "ingredients_snapshot": [
                    {"ingredient": {"ingredient_id": 101}}
                ],
            }
            if cache
            else None
        ),
    }


def item_task(*, exact_complete: bool, flagged_anomaly: bool = False) -> ItemTask:
    prepared = PreparedItem(
        item_id="scan-123:0",
        source_index=0,
        raw_menu_name="김치찌개",
        normalized_menu_name="김치찌개",
        display_name="김치찌개",
        description=None,
        price_text=None,
        price_options=[],
        ocr_confidence=0.98,
        locale_hint="ko",
        options=[],
        residual_tokens=[],
        risk_tag_observations=[],
    )
    candidate = {
        "menu_id": 1001,
        "menu_category_id": 11,
        "canonical_name": "김치찌개",
        "score": 1.0,
        "match_type": "canonical",
    }
    selection = CandidateSelectionResult.model_validate(
        {
            **prepared.model_dump(mode="json"),
            "candidate_status": "ok",
            "match_candidates": [candidate],
            "normalization_status": "normalized",
            "selection_method": "deterministic",
            "selection_reason_codes": ["exact_unique"],
            "selected_candidate": candidate,
        }
    )
    confirmation = {
        "ingredient": {"ingredient_id": 101, "canonical_name": "돼지고기"},
        "constraint_tags": [],
        "status": "present",
        "evidence": evidence("owner:pork", "owner_feedback"),
        "flagged_anomaly": flagged_anomaly,
        "override_eligible": True,
        "expired": False,
    }
    selected = CandidateKnowledge.model_validate(
        {
            "menu_id": 1001,
            "exact_feedback": {
                "status": "ok",
                "exact": {
                    "menu_id": 1001,
                    "confirmations": [confirmation],
                    "flag_confirmations": [],
                    "owner_question_states": [],
                },
            },
            "ontology": ontology_slice(cache=exact_complete),
            "priors": {"status": "not_found", "store": [], "evidence": []},
        }
    )
    graph = IngredientGraph.model_validate(
        {
            "status": "ok",
            "ingredients": [
                {
                    "ingredient_id": 101,
                    "canonical_name": "돼지고기",
                    "name_normalized": "돼지고기",
                    "localized_name": "돼지고기",
                    "taxonomy_category": "base",
                },
                {
                    "ingredient_id": 102,
                    "canonical_name": "액젓",
                    "name_normalized": "액젓",
                    "localized_name": "액젓",
                    "taxonomy_category": "fermented",
                },
            ],
            "aliases": [],
            "tags": [
                {"ingredient_id": 101, "tag_code": "is_pork"},
                {"ingredient_id": 102, "tag_code": "is_fish"},
            ],
            "compositions": [],
            "composition_tags": [],
        }
    )
    return ItemTask(
        prepared_item=prepared,
        selection=selection,
        knowledge=ItemTaskKnowledge(
            item_id=prepared.item_id,
            selected=selected,
            fallback_ontology=selected.ontology,
            web_search_cache={
                "status": "not_found",
                "cached_sources": [],
                "next_rank": 1,
            },
        ),
        ingredient_graph=graph,
        source_reliability=SourceReliability(
            status="ok",
            weights=[{"source_key": "corpus:recipes", "weight": 1.0}],
        ),
        user_profile=UserProfile(
            religion_type="halal",
            is_vegetarian=False,
            vegetarian_type=None,
            no_alcohol=False,
            allergies=[],
            no_spicy=False,
        ),
        locale="ko",
    )


class RecordingOntology:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        recipe_evidence = evidence("recipe:fish")
        return OntologyResponse(
            context=request.context,
            menu=OntologyResultMenu(
                menu_id=1001,
                base_menu_id=None,
                remain_token=None,
                menu_category_id=11,
                menu_category="찌개",
                exists_in_db=True,
                is_variant=False,
                variant_origin=None,
            ),
            menu_ambiguity_flags=[],
            ingredients=[
                OntologyIngredient(
                    ingredient={"ingredient_id": 102, "canonical_name": "액젓"},
                    taxonomy_category="fermented",
                    constraint_tags=["is_fish"],
                    role="hidden",
                    depth=1,
                    parent_ingredient=None,
                    source="expanded",
                    curated=True,
                    observation_basis="direct",
                    observations=[{"corpus": "recipes", "k_count": 4, "n_total": 10}],
                    anomaly_locked=False,
                    evidence_refs=["recipe:fish"],
                )
            ],
            evidence=[recipe_evidence],
            variant_suggestion=None,
            unmapped_tokens=[],
            cache_command=(
                {
                    "command_id": "cache:scan-123:0",
                    "operation_id": None,
                    "item_id": "scan-123:0",
                    "card_id": None,
                    "action": "save_menu_ingredient_cache",
                    "idempotency_key": "sha256:" + "a" * 64,
                    "payload": {
                        "menu_id": 1001,
                        "menu_revision": 7,
                        "ingredients_snapshot": [],
                    },
                }
                if request.context.store_id is not None
                else None
            ),
            warnings=request.warnings,
            errors=request.errors,
        )


class RecordingBayesian:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return BayesianResponse(
            context=request.context,
            probabilities=[
                ProbabilityResult(
                    ingredient=item.ingredient,
                    constraint_tags=item.constraint_tags,
                    role=item.role,
                    posterior_mean=0.4,
                    alpha_post=5.0,
                    beta_post=7.0,
                    prior_source="global",
                    source=item.source,
                    depth=item.depth,
                    confidence=0.7,
                    anomaly_locked=item.anomaly_locked,
                    evidence_refs=item.evidence_refs,
                )
                for item in request.ingredients
            ],
            warnings=request.warnings,
            errors=request.errors,
        )


class RecordingTagLookup:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return TagLookupResponse(
            context=request.context,
            results=[
                TagLookupResult(
                    ingredient_id=ingredient_id,
                    constraint_tags=["is_pork"],
                    role="identity",
                )
                for ingredient_id in request.ingredient_ids
            ],
            warnings=request.warnings,
            errors=request.errors,
        )


class RuleDecision:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        outcome = decide(request)
        return DecisionResponse(
            context=request.context,
            menu=request.menu,
            risk_level=outcome.risk_level,
            evidence_basis=outcome.evidence_basis,
            information_status=outcome.information_status,
            risk_ingredients=outcome.risk_ingredients,
            matched_tags=outcome.matched_tags,
            evidence_refs=outcome.evidence_refs,
            message={
                "verdict": "확인이 필요해요.",
                "reason": None,
                "notes": [],
            },
            owner_cards=[],
            high_risk_review=outcome.high_risk_review,
            warnings=request.warnings,
            errors=request.errors,
        )


class UnsafeDecision(RuleDecision):
    async def run(self, request):
        response = await super().run(request)
        return response.model_copy(update={"risk_level": "safe"})


def analyzer():
    ontology = RecordingOntology()
    bayesian = RecordingBayesian()
    tags = RecordingTagLookup()
    decision = RuleDecision()
    item_analyzer = ItemGraphAnalyzer(
        ItemGraphDependencies(
            ontology=ontology,
            bayesian=bayesian,
            tag_lookup=tags,
            decision=decision,
            now_factory=lambda: NOW,
        )
    )
    return item_analyzer, ontology, bayesian, tags, decision


def context(store_id: int | None = 123) -> RunContext:
    return RunContext(
        schema_version="3.0",
        trace_id=TRACE_ID,
        scan_session_id="scan-123",
        store_id=store_id,
    )


def test_exact_complete_path_skips_ontology_and_bayesian() -> None:
    item_analyzer, ontology, bayesian, tags, decision = analyzer()

    output = asyncio.run(item_analyzer.run(item_task(exact_complete=True), context()))

    assert output.result.route == "exact_only"
    assert output.result.risk_level == "danger"
    assert ontology.requests == []
    assert bayesian.requests == []
    assert tags.requests[0].ingredient_ids == [101]
    assert decision.requests[0].confirmed_results[0].constraint_tags == ["is_pork"]
    assert [item.evidence_id for item in output.evidence] == ["owner:pork"]


def test_partial_exact_uses_db_ontology_and_bayesian_path() -> None:
    item_analyzer, ontology, bayesian, tags, decision = analyzer()

    output = asyncio.run(item_analyzer.run(item_task(exact_complete=False), context()))

    assert output.result.route == "db_bayes"
    assert output.result.risk_level == "danger"
    assert [item.ingredient.ingredient_id for item in ontology.requests[0].confirmed_ingredients] == [101]
    assert bayesian.requests[0].ingredients[0].ingredient.ingredient_id == 102
    assert tags.requests[0].ingredient_ids == [101]
    assert decision.requests[0].probability_results[0].constraint_tags == ["is_fish"]
    assert [item.evidence_id for item in output.evidence] == [
        "owner:pork",
        "recipe:fish",
    ]
    assert [command.action for command in output.commands] == [
        "save_menu_ingredient_cache"
    ]


def test_storeless_db_path_skips_exact_and_blocks_safe() -> None:
    item_analyzer, ontology, bayesian, tags, decision = analyzer()
    task = item_task(exact_complete=False)
    selected = task.knowledge.selected
    assert selected is not None
    task = replace(
        task,
        knowledge=replace(
            task.knowledge,
            selected=selected.model_copy(
                update={
                    "exact_feedback": ExactFeedbackSlice(
                        status="not_found", exact=None
                    )
                }
            ),
        ),
    )

    output = asyncio.run(item_analyzer.run(task, context(store_id=None)))

    assert ontology.requests[0].confirmed_ingredients == []
    assert tags.requests == []
    assert decision.requests[0].owner_interaction_allowed is False
    assert output.result.risk_level == "caution"
    assert any(
        warning.code == "store_context_missing"
        for warning in output.result.warnings
    )
    assert output.commands == ()


def test_anomaly_confirmation_is_forwarded_to_ontology_lock() -> None:
    item_analyzer, ontology, _, _, decision = analyzer()

    output = asyncio.run(
        item_analyzer.run(
            item_task(exact_complete=True, flagged_anomaly=True), context()
        )
    )

    request = ontology.requests[0]
    assert request.confirmed_ingredients == []
    assert request.anomaly_locked_ingredients[0].ingredient_id == 101
    assert decision.requests[0].confirmed_results[0].override_eligible is False
    assert output.result.risk_level != "safe"


def test_runtime_variant_waits_for_phase_seven_instead_of_skipping_review() -> None:
    item_analyzer, ontology, _, _, _ = analyzer()

    async def runtime_variant(request):
        response = await RecordingOntology().run(request)
        return response.model_copy(
            update={
                "menu": OntologyResultMenu(
                    menu_id=None,
                    base_menu_id=1001,
                    remain_token="차돌",
                    menu_category_id=11,
                    menu_category="찌개",
                    exists_in_db=False,
                    is_variant=True,
                    variant_origin="runtime_tagged",
                ),
                "variant_suggestion": {
                    "base_menu_id": 1001,
                    "remain_token": "차돌",
                    "suggested_ingredients": [],
                    "evidence": evidence("variant:beef"),
                },
            }
        )

    ontology.run = runtime_variant

    with pytest.raises(ItemAnalysisError, match="7번 구현"):
        asyncio.run(item_analyzer.run(item_task(exact_complete=False), context()))


def test_item_graph_rejects_safe_result_from_probability_path() -> None:
    ontology = RecordingOntology()
    bayesian = RecordingBayesian()
    tags = RecordingTagLookup()
    item_analyzer = ItemGraphAnalyzer(
        ItemGraphDependencies(
            ontology=ontology,
            bayesian=bayesian,
            tag_lookup=tags,
            decision=UnsafeDecision(),
            now_factory=lambda: NOW,
        )
    )

    with pytest.raises(ItemGraphInvariantError, match="safety restriction"):
        asyncio.run(
            item_analyzer.run(item_task(exact_complete=False), context())
        )


def test_item_graph_compiles_with_documented_db_nodes() -> None:
    item_analyzer, *_ = analyzer()

    assert {
        "prepare_item",
        "exact",
        "ontology",
        "bayesian",
        "tag_lookup",
        "decision",
        "finish_item",
    } <= set(item_analyzer.graph.get_graph().nodes)
