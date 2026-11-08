"""Item Graph에서 ③ Exact Feedback을 부르는 계약 (#113, docs/3-tool-exact.md §3)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import ai_supervisor.item_graph as item_graph_module
from ai_supervisor.contracts.exact import ExactFeedbackResponse
from ai_supervisor.contracts.external import ExactFeedbackSlice
from ai_supervisor.routers import route_after_exact, route_item_start
from ai_supervisor.tests.test_item_graph import (
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


def test_complete_exact_route_ignores_legacy_override_flag() -> None:
    legacy_confirmation = {
        **confirmation(101),
        "override_eligible": False,
    }
    run_context = context()
    response = ExactFeedbackResponse.model_validate(
        {
            "context": run_context.node_context(
                "item", "scan-123:0", include_store=True
            ),
            "menu_id": 1001,
            "confirmation_scope": "exact",
            "completeness": "complete",
            "confirmations": [legacy_confirmation],
            "flag_confirmations": [],
            "warnings": [],
            "errors": [],
        }
    )

    assert route_after_exact(response) == "tag_lookup"


def test_supervisor_normalizes_known_legacy_answers_as_confirmed(
    monkeypatch,
) -> None:
    class LegacyExactFeedbackTool:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, request):
            ingredient = {
                **confirmation(101),
                "override_eligible": False,
            }
            flag_evidence = evidence("owner:flag", "owner_feedback")
            return ExactFeedbackResponse.model_validate(
                {
                    "context": request.context.model_dump(mode="json"),
                    "menu_id": request.menu.menu_id,
                    "confirmation_scope": "exact",
                    "completeness": "complete",
                    "confirmations": [ingredient],
                    "flag_confirmations": [
                        {
                            "ambiguity_flag": "has_unclear_jeotgal",
                            "constraint_tags": ["is_fish"],
                            "status": "absent",
                            "evidence": flag_evidence,
                            "override_eligible": False,
                        }
                    ],
                    "warnings": [],
                    "errors": [],
                }
            )

    monkeypatch.setattr(
        item_graph_module,
        "ExactFeedbackTool",
        LegacyExactFeedbackTool,
    )
    item_analyzer, ontology, bayesian, _, decision = analyzer()

    asyncio.run(item_analyzer.run(item_task(exact_complete=True), context()))

    assert ontology.requests == []
    assert bayesian.requests == []
    decision_request = decision.requests[0]
    assert decision_request.confirmed_results[0].override_eligible is True
    assert decision_request.flag_confirmations[0].override_eligible is True


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


def test_flagged_answer_reaches_ontology_as_a_confirmation() -> None:
    # No anomaly lock (10/27 meeting): a flagged answer is confirmed like any other,
    # so ④ drops it from the probability list instead of locking it.
    item_analyzer, ontology, _, _, decision = analyzer()
    flagged = {**confirmation(101, status="absent"), "flagged_anomaly": True}
    task = with_exact(
        item_task(exact_complete=False),
        exact_slice(flagged, confirmation(102, status="unknown")),
    )

    asyncio.run(item_analyzer.run(task, context()))

    request = ontology.requests[0]
    assert [item.ingredient.ingredient_id for item in request.confirmed_ingredients] == [101]
    assert request.anomaly_locked_ingredients == []
    assert not any(r.anomaly_locked for r in decision.requests[0].probability_results)
