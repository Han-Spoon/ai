from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

import pytest

from ai_decision_policy import decide
from ai_normalization.contracts import CandidateSelectionResult
from ai_supervisor.contracts.bayesian import BayesianResponse, ProbabilityResult
from ai_supervisor.contracts.db_update import DbUpdateResponse
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
                {
                    "ingredient_id": 120,
                    "canonical_name": "소고기",
                    "name_normalized": "소고기",
                    "localized_name": "소고기",
                    "taxonomy_category": "base",
                },
            ],
            "aliases": [],
            "tags": [
                {"ingredient_id": 101, "tag_code": "is_pork"},
                {"ingredient_id": 102, "tag_code": "is_fish"},
                {"ingredient_id": 120, "tag_code": "is_beef"},
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


class UnusedAdapter:
    async def run(self, request):
        raise AssertionError("adapter must not be called on the DB path")


class RuntimeVariantOntology(RecordingOntology):
    async def run(self, request):
        response = await super().run(request)
        variant_evidence = {
            "evidence_id": "variant:beef",
            "evidence_class": "soft",
            "source_type": "ocr_variant_tag",
            "source_ref": "scan-123:0:차돌",
            "verification_status": "pending_review",
            "reliability_weight": 0.3,
            "observed_at": "2026-10-01T00:00:00Z",
        }
        payload = response.model_dump(mode="json")
        payload.update(
            {
                "menu": {
                    "menu_id": None,
                    "base_menu_id": 1001,
                    "remain_token": "차돌",
                    "menu_category_id": 11,
                    "menu_category": "찌개",
                    "exists_in_db": False,
                    "is_variant": True,
                    "variant_origin": "runtime_tagged",
                },
                "ingredients": [
                    *payload["ingredients"],
                    {
                        "ingredient": {
                            "ingredient_id": 120,
                            "canonical_name": "소고기",
                        },
                        "taxonomy_category": "base",
                        "constraint_tags": ["is_beef"],
                        "role": "identity",
                        "depth": 0,
                        "parent_ingredient": None,
                        "source": "variant_suggested",
                        "curated": False,
                        "observation_basis": "none",
                        "observations": [],
                        "anomaly_locked": False,
                        "evidence_refs": ["variant:beef"],
                    },
                ],
                "evidence": [*payload["evidence"], variant_evidence],
                "variant_suggestion": {
                    "base_menu_id": 1001,
                    "remain_token": "차돌",
                    "suggested_ingredients": [
                        {
                            "ingredient": {
                                "ingredient_id": 120,
                                "canonical_name": "소고기",
                            },
                            "constraint_tags": ["is_beef"],
                            "role": "identity",
                        }
                    ],
                    "evidence": variant_evidence,
                },
                "cache_command": None,
            }
        )
        return OntologyResponse.model_validate(payload)


class VariantDbUpdate:
    def __init__(self, *, source_type: str = "ocr_variant_tag") -> None:
        self.requests = []
        self.source_type = source_type

    async def run(self, request):
        self.requests.append(request)
        operation = request.operations[0]
        suggestion = operation.payload.variant_suggestion
        assert suggestion is not None
        payload = operation.payload.model_dump(mode="json")
        payload["provenance"] = {
            "origin_node": "ontology",
            "source_type": self.source_type,
            "evidence_class": "soft",
            "evidence_refs": [suggestion.evidence.evidence_id],
        }
        return DbUpdateResponse.model_validate(
            {
                "context": request.context.model_dump(mode="json"),
                "commands": [
                    {
                        "command_id": "command-variant-review",
                        "operation_id": operation.operation_id,
                        "item_id": request.context.item_id,
                        "card_id": None,
                        "action": operation.operation_type,
                        "idempotency_key": "sha256:"
                        + sha256(operation.operation_id.encode()).hexdigest(),
                        "payload": payload,
                    }
                ],
                "warnings": [
                    item.model_dump(mode="json") for item in request.warnings
                ],
                "errors": [
                    item.model_dump(mode="json") for item in request.errors
                ],
            }
        )


def variant_analyzer(db_update=None):
    ontology = RuntimeVariantOntology()
    bayesian = RecordingBayesian()
    tags = RecordingTagLookup()
    decision = RuleDecision()
    db_update = db_update or VariantDbUpdate()
    item_analyzer = ItemGraphAnalyzer(
        ItemGraphDependencies(
            ontology=ontology,
            bayesian=bayesian,
            tag_lookup=tags,
            decision=decision,
            web_collect=UnusedAdapter(),
            ingredient_mapping=UnusedAdapter(),
            web_aggregate=UnusedAdapter(),
            db_update=db_update,
            now_factory=lambda: NOW,
        )
    )
    return item_analyzer, ontology, bayesian, decision, db_update


def runtime_variant_task() -> ItemTask:
    task = item_task(exact_complete=False)
    selected = task.knowledge.selected
    assert selected is not None
    return replace(
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
        user_profile=task.user_profile.model_copy(
            update={"religion_type": None, "allergies": ["is_beef"]}
        ),
    )


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
            web_collect=UnusedAdapter(),
            ingredient_mapping=UnusedAdapter(),
            web_aggregate=UnusedAdapter(),
            db_update=UnusedAdapter(),
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
    assert output.result.owner_answer_status == "complete"
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
    assert output.result.owner_answer_status == "partial"
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


def test_runtime_variant_reviews_then_continues_to_bayesian_and_decision() -> None:
    item_analyzer, _, bayesian, decision, db_update = variant_analyzer()

    output = asyncio.run(item_analyzer.run(runtime_variant_task(), context()))

    request = db_update.requests[0]
    assert [operation.operation_type for operation in request.operations] == [
        "create_review_item"
    ]
    payload = request.operations[0].payload
    assert payload.review_origin == "variant_suggestion"
    assert payload.web_sources == []
    assert payload.review_notes == []
    assert payload.variant_suggestion.remain_token == "차돌"
    assert bayesian.requests[0].ingredients[-1].source == "variant_suggested"
    assert [
        item.ingredient.ingredient_id
        for item in decision.requests[0].proposed_variant_ingredients
    ] == [120]
    assert [
        item.ingredient.ingredient_id
        for item in decision.requests[0].probability_results
    ] == [102]
    assert output.result.route == "db_variant_bayes"
    assert output.result.risk_level == "caution"
    assert [command.action for command in output.commands] == [
        "create_review_item"
    ]
    assert "variant:beef" in [item.evidence_id for item in output.evidence]


def test_storeless_runtime_variant_skips_review_but_keeps_analysis() -> None:
    item_analyzer, _, bayesian, decision, db_update = variant_analyzer()

    output = asyncio.run(
        item_analyzer.run(runtime_variant_task(), context(store_id=None))
    )

    assert db_update.requests == []
    assert bayesian.requests[0].context.store_id is None
    assert decision.requests[0].proposed_variant_ingredients
    assert output.result.route == "db_variant_bayes"
    assert output.result.risk_level == "caution"
    assert output.commands == ()


def test_runtime_variant_rejects_changed_review_provenance() -> None:
    item_analyzer, *_ = variant_analyzer(
        VariantDbUpdate(source_type="web_search")
    )

    with pytest.raises(
        ItemGraphInvariantError, match="changed operation provenance"
    ):
        asyncio.run(item_analyzer.run(runtime_variant_task(), context()))


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
            web_collect=UnusedAdapter(),
            ingredient_mapping=UnusedAdapter(),
            web_aggregate=UnusedAdapter(),
            db_update=UnusedAdapter(),
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
        "variant_review",
        "web_search",
        "ingredient_mapping",
        "web_aggregate",
        "web_review",
        "bayesian",
        "tag_lookup",
        "decision",
        "finish_item",
    } <= set(item_analyzer.graph.get_graph().nodes)
