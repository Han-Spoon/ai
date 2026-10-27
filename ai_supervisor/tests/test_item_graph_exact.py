"""Item Graph에서 ③ Exact Feedback을 부르는 계약 (#113, docs/3-tool-exact.md §3)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_supervisor.contracts.external import ExactFeedbackSlice
from ai_supervisor.contracts.ontology import OntologyIngredient
from ai_supervisor.routers import route_item_start
from ai_supervisor.tests.test_item_graph import (
    RecordingOntology,
    analyzer,
    context,
    evidence,
    item_task,
)


def with_exact(task, exact_feedback: ExactFeedbackSlice):
    selected = task.knowledge.selected
    assert selected is not None
    return replace(
        task,
        knowledge=replace(
            task.knowledge,
            selected=selected.model_copy(update={"exact_feedback": exact_feedback}),
        ),
    )


def confirmation(
    ingredient_id: int,
    *,
    status: str = "present",
    observed_at: str = "2026-10-01T00:00:00Z",
) -> dict:
    owner_evidence = evidence(f"owner:{ingredient_id}", "owner_feedback")
    owner_evidence["observed_at"] = observed_at
    return {
        "ingredient": {"ingredient_id": ingredient_id, "canonical_name": f"재료{ingredient_id}"},
        # ③이 비워 돌려주고 ④가 채운다. 백엔드가 보낸 값은 무시된다.
        "constraint_tags": ["is_beef"],
        "status": status,
        "evidence": owner_evidence,
        "flagged_anomaly": False,
        "override_eligible": True,
    }


def exact_slice(*confirmations: dict) -> ExactFeedbackSlice:
    return ExactFeedbackSlice.model_validate(
        {
            "status": "ok",
            "exact": {
                "menu_id": 1001,
                "confirmations": list(confirmations),
                "flag_confirmations": [],
            },
        }
    )


def test_exact_runs_only_with_store_and_selected_menu() -> None:
    task = item_task(exact_complete=False)
    no_menu = replace(task, knowledge=replace(task.knowledge, selected=None))

    assert route_item_start(task, 123) == "exact"
    assert route_item_start(task, None) == "ontology"
    assert route_item_start(no_menu, 123) == "ontology"


def test_unavailable_exact_slice_reads_as_empty_and_blocks_safe() -> None:
    item_analyzer, ontology, _, tags, decision = analyzer()
    task = with_exact(
        item_task(exact_complete=True),
        ExactFeedbackSlice(status="unavailable", exact=None),
    )

    output = asyncio.run(item_analyzer.run(task, context()))

    assert ontology.requests[0].confirmed_ingredients == []
    assert tags.requests == []
    request = decision.requests[0]
    assert request.confirmed_results == []
    assert any(
        warning.code == "knowledge_slice_unavailable" and warning.forces_caution
        for warning in request.warnings
    )
    assert request.information_status == "partial"
    assert output.result.risk_level != "safe"
    assert output.result.owner_answer_status == "none"


def test_only_eligible_known_confirmations_skip_bayesian() -> None:
    item_analyzer, ontology, _, tags, decision = analyzer()
    task = with_exact(
        item_task(exact_complete=False),
        exact_slice(
            confirmation(101),
            confirmation(102, status="unknown"),
            # Owner answers never expire, so a year-old answer still overrides (③ §1-7).
            confirmation(103, status="absent", observed_at="2025-10-01T00:00:00Z"),
        ),
    )

    output = asyncio.run(item_analyzer.run(task, context()))

    request = ontology.requests[0]
    assert [item.ingredient.ingredient_id for item in request.confirmed_ingredients] == [101, 103]
    assert request.anomaly_locked_ingredients == []
    assert tags.requests[0].ingredient_ids == [101, 102, 103]
    confirmed = decision.requests[0].confirmed_results
    assert [(item.status, item.override_eligible) for item in confirmed] == [
        ("present", True),
        ("unknown", True),
        ("absent", True),
    ]
    assert all(item.constraint_tags == ["is_pork"] for item in confirmed)
    assert output.result.route == "db_bayes"
    assert output.result.owner_answer_status == "partial"


def test_anomaly_lock_reaches_decision_through_ontology_and_bayesian() -> None:
    item_analyzer, ontology, bayesian, _, decision = analyzer()

    async def lock_anomalies(request):
        response = await RecordingOntology().run(request)
        locked = [
            OntologyIngredient(
                ingredient=ref,
                taxonomy_category="base",
                constraint_tags=["is_pork"],
                role="identity",
                depth=0,
                parent_ingredient=None,
                source="recipe",
                curated=True,
                observation_basis="direct",
                observations=[{"corpus": "recipes", "k_count": 9, "n_total": 10}],
                anomaly_locked=True,
                evidence_refs=["recipe:fish"],
            )
            for ref in request.anomaly_locked_ingredients
        ]
        return response.model_copy(
            update={"ingredients": [*locked, *response.ingredients]}
        )

    ontology.run = lock_anomalies

    output = asyncio.run(
        item_analyzer.run(
            item_task(exact_complete=True, flagged_anomaly=True), context()
        )
    )

    assert bayesian.requests[0].ingredients[0].anomaly_locked is True
    locked = [
        item.ingredient.ingredient_id
        for item in decision.requests[0].probability_results
        if item.anomaly_locked
    ]
    assert locked == [101]
    assert output.result.risk_level != "safe"
