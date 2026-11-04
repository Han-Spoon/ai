from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from ai_supervisor.adapters import LegacyOcrAdapter
from ai_supervisor.contracts.decision import DecisionResponse
from ai_supervisor.contracts.external import PrepareRequest
from ai_supervisor.contracts.web_search import WebCollectResponse, WebMenu
from ai_supervisor.service import SupervisorService, SupervisorServiceDependencies
from ai_supervisor.tests.test_judge_graph import request as judge_request


TRACE_ID = UUID("0199a2f0-0000-7000-8000-000000000001")
PREPARE_ID = UUID("0199a2f0-0000-7000-8000-000000000101")


class UnusedAdapter:
    async def run(self, request):
        raise AssertionError("adapter must not be called by Prepare")


class NoSourceWebCollect:
    async def run(self, request):
        return WebCollectResponse(
            context=request.context,
            menu=WebMenu(
                menu_id=request.menu.menu_id,
                normalized_menu_name=request.menu.normalized_menu_name,
            ),
            found=False,
            sources=[],
            warnings=request.warnings,
            errors=request.errors,
        )


class CautionDecision:
    async def run(self, request):
        return DecisionResponse(
            context=request.context,
            menu=request.menu,
            risk_level="caution",
            evidence_basis="unknown",
            information_status=request.information_status,
            risk_ingredients=[],
            matched_tags=[],
            evidence_refs=[],
            message={
                "verdict": "확인이 필요해요.",
                "reason": None,
                "notes": [],
            },
            owner_cards=[],
            high_risk_review={"required": False, "reason_code": None},
            warnings=request.warnings,
            errors=request.errors,
        )


def test_supervisor_service_runs_prepare_with_compiled_dependencies(caplog) -> None:
    async def ocr_runner(image):
        return {
            "final": {
                "scan_quality": {
                    "status": "usable",
                    "score": 95,
                    "reasons": [],
                    "retake_suggestions": [],
                },
                "menu_analyses": [
                    {
                        "display_order": 1,
                        "menu_name_ko": "■ 김치 찌개 8,000원",
                        "description_ko": None,
                        "price_text": "8,000원",
                        "price_options": [],
                        "ocr_confidence": 0.95,
                        "is_spicy": False,
                    }
                ],
            }
        }

    unused = UnusedAdapter()
    service = SupervisorService(
        SupervisorServiceDependencies(
            ocr=LegacyOcrAdapter(ocr_runner),
            web_collect=unused,
            decision=unused,
            high_risk_review=unused,
            web_aggregate=unused,
            prepare_id_factory=lambda: PREPARE_ID,
        )
    )
    request = PrepareRequest(
        schema_version="3.0",
        trace_id=TRACE_ID,
        scan_session_id="scan-123",
        image={
            "source": "upload",
            "storage_key": "scans/menu.jpg",
            "version_id": None,
            "expected_etag": None,
        },
        locale="ko",
    )

    with caplog.at_level(logging.INFO, logger="ai_supervisor.service"):
        response = asyncio.run(service.prepare(request))

    assert response.prepare_id == PREPARE_ID
    assert response.prepared_items[0].display_name == "김치 찌개"
    assert response.prepared_items[0].normalized_menu_name == "김치찌개"
    assert response.prepared_items[0].price_text == "8,000원"
    assert {"ocr", "clean_names", "finalize_prepared"} <= set(
        service.prepare_graph.get_graph().nodes
    )
    assert {"select_candidates", "analyze_item", "finalize"} <= set(
        service.judge_graph.get_graph().nodes
    )
    record = next(
        record for record in caplog.records if record.message == "supervisor_prepare_complete"
    )
    assert record.trace_id == str(TRACE_ID)
    assert record.prepared_item_count == 1
    assert record.scan_quality_status == "usable"
    assert record.latency_ms >= 0


def test_supervisor_service_runs_storeless_judge_through_injected_adapters(caplog) -> None:
    unused = UnusedAdapter()
    service = SupervisorService(
        SupervisorServiceDependencies(
            ocr=unused,
            web_collect=NoSourceWebCollect(),
            decision=CautionDecision(),
            high_risk_review=unused,
            web_aggregate=unused,
        )
    )
    source = judge_request(count=1, store_id=None)

    with caplog.at_level(logging.INFO, logger="ai_supervisor.service"):
        response = asyncio.run(service.judge(source))

    assert response.store_id is None
    assert response.items[0].risk_level == "caution"
    assert response.items[0].route == "no_information"
    assert response.persistence_commands == []
    record = next(
        record for record in caplog.records if record.message == "supervisor_judge_complete"
    )
    assert record.trace_id == str(source.trace_id)
    assert record.item_count == 1
    assert record.route_counts == {"no_information": 1}
    assert record.risk_level_counts == {"caution": 1}
    assert record.latency_ms >= 0
