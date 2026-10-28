"""``ExactFeedbackTool.run`` wraps the core in the ⓪ §4-2 contract."""

import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ai_exact_feedback import ExactFeedbackTool
from ai_supervisor.contracts.exact import ExactFeedbackRequest
from ai_supervisor.contracts.external import CandidateKnowledge, OntologySlice, WebSearchCache
from ai_supervisor.state import ItemTaskKnowledge

NOW = datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc)
RECENT = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
YEAR_AGO = datetime(2025, 10, 8, 3, 0, tzinfo=timezone.utc)
PORK, KIMCHI = 101, 109
NAMES = {PORK: "돼지고기", KIMCHI: "김치"}

EMPTY_ONTOLOGY = {
    "status": "not_found",
    "menu": None,
    "recipe_ingredients": [],
    "menu_recipe_corpora": [],
    "menu_ingredient_priors": [],
    "cache": None,
}


def confirmation(ingredient_id: int, status: str = "present", observed_at: datetime = RECENT) -> dict:
    return {
        "ingredient": {"ingredient_id": ingredient_id, "canonical_name": NAMES[ingredient_id]},
        "constraint_tags": [],
        "status": status,
        "evidence": {
            "evidence_id": f"evidence-{ingredient_id}",
            "evidence_class": "hard",
            "source_type": "owner_feedback",
            "source_ref": f"ingredient_confirmations:{ingredient_id}",
            "verification_status": "owner_confirmed",
            "reliability_weight": 1.0,
            "observed_at": observed_at.isoformat(),
        },
        "flagged_anomaly": False,
        "override_eligible": True,
        "expired": False,
    }


def ontology_with_cache(*ingredient_ids: int) -> dict:
    return {
        "status": "ok",
        "menu": {
            "menu_id": 1001,
            "base_menu_id": None,
            "remain_token": None,
            "menu_category_id": None,
            "source": "curated",
            "ambiguity_flags": [],
            "knowledge_revision": 7,
        },
        "recipe_ingredients": [{"ingredient_id": i} for i in ingredient_ids],
        "menu_recipe_corpora": [],
        "menu_ingredient_priors": [],
        "cache": {
            "menu_revision": 7,
            "ingredients_snapshot": [
                {"ingredient": {"ingredient_id": i, "canonical_name": NAMES[i]}} for i in ingredient_ids
            ],
        },
    }


def knowledge(
    confirmations: list[dict] | None,
    *,
    ontology: dict | None = None,
    exact_menu_id: int = 1001,
    selected: bool = True,
) -> ItemTaskKnowledge:
    exact = (
        {"status": "not_found", "exact": None}
        if confirmations is None
        else {
            "status": "ok",
            "exact": {
                "menu_id": exact_menu_id,
                "confirmations": confirmations,
                "flag_confirmations": [],
                "owner_question_states": [],
            },
        }
    )
    candidate = CandidateKnowledge.model_validate(
        {
            "menu_id": 1001,
            "exact_feedback": exact,
            "ontology": ontology or EMPTY_ONTOLOGY,
            "priors": {"status": "not_found", "store": [], "evidence": []},
        }
    )
    return ItemTaskKnowledge(
        item_id="scan-123:0",
        selected=candidate if selected else None,
        fallback_ontology=OntologySlice.model_validate(EMPTY_ONTOLOGY),
        web_search_cache=WebSearchCache(status="not_found", cached_sources=[]),
    )


def request(*, item_id: str = "scan-123:0", menu_id: int = 1001, store_id: int | None = 123456) -> ExactFeedbackRequest:
    return ExactFeedbackRequest.model_validate(
        {
            "context": {
                "schema_version": "3.0",
                "trace_id": "0199a2f0-0000-7000-8000-000000000001",
                "scan_session_id": "scan-123",
                "store_id": store_id,
                "call_scope": "item",
                "item_id": item_id,
            },
            "menu": {"menu_id": menu_id, "menu_category_id": None, "normalized_menu_name": "김치찌개"},
            "scope_hint": "exact",
            "warnings": [
                {
                    "code": "knowledge_slice_unavailable",
                    "node": "supervisor",
                    "item_id": item_id,
                    "message": "priors unavailable",
                    "forces_caution": True,
                }
            ],
            "errors": [],
        }
    )


def run(tool: ExactFeedbackTool, req: ExactFeedbackRequest):
    return asyncio.run(tool.run(req))


def test_run_returns_contract_response() -> None:
    tool = ExactFeedbackTool(
        knowledge([confirmation(PORK, "absent"), confirmation(KIMCHI)], ontology=ontology_with_cache(PORK, KIMCHI))
    )
    req = request()
    response = run(tool, req)
    assert response.context == req.context
    assert (response.menu_id, response.confirmation_scope, response.completeness) == (1001, "exact", "complete")
    assert [c.ingredient.ingredient_id for c in response.confirmations] == [PORK, KIMCHI]
    assert response.warnings == req.warnings
    assert response.errors == []


def test_run_accepts_the_item_graph_clock_but_never_expires() -> None:
    # The Item Graph still passes a clock (§8); owner answers do not expire (§1-7).
    tool = ExactFeedbackTool(
        knowledge([confirmation(PORK, observed_at=YEAR_AGO)], ontology=ontology_with_cache(PORK)),
        clock=lambda: NOW,
    )
    response = run(tool, request())
    assert (response.confirmations[0].expired, response.confirmations[0].override_eligible) == (False, True)
    assert response.completeness == "complete"


def test_run_without_records_is_empty_unknown() -> None:
    response = run(ExactFeedbackTool(knowledge(None)), request())
    assert response.confirmations == []
    assert response.completeness == "unknown"


def test_06_a_storeless_request_never_reaches_the_tool() -> None:
    with pytest.raises(ValidationError):
        request(store_id=None)


def test_run_rejects_another_items_knowledge() -> None:
    tool = ExactFeedbackTool(knowledge([confirmation(PORK)]))
    with pytest.raises(ValueError, match="own item_id"):
        run(tool, request(item_id="scan-123:1"))


def test_run_rejects_menu_other_than_selected() -> None:
    tool = ExactFeedbackTool(knowledge([confirmation(PORK)]))
    with pytest.raises(ValueError, match="no selected knowledge"):
        run(tool, request(menu_id=2002))


def test_run_rejects_unmatched_item() -> None:
    tool = ExactFeedbackTool(knowledge([confirmation(PORK)], selected=False))
    with pytest.raises(ValueError, match="no selected knowledge"):
        run(tool, request())


def test_run_rejects_confirmations_of_another_menu() -> None:
    tool = ExactFeedbackTool(knowledge([confirmation(PORK)], exact_menu_id=2002))
    with pytest.raises(ValueError, match="different menu_id"):
        run(tool, request())
