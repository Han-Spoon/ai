from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
from fastapi import FastAPI

from ai_supervisor.api import router
from ai_supervisor.contracts.external import PrepareRequest, PrepareResponse
from ai_supervisor.hashing import ContractHashMismatch, prepared_items_hash
from ai_supervisor.tests.test_judge_graph import request as judge_request


TRACE_ID = UUID("0199a2f0-0000-7000-8000-000000000001")
PREPARE_ID = UUID("0199a2f0-0000-7000-8000-000000000101")


def prepare_request() -> PrepareRequest:
    return PrepareRequest(
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


def prepare_response(request: PrepareRequest) -> PrepareResponse:
    items = [
        {
            "item_id": "scan-123:0",
            "source_index": 0,
            "raw_menu_name": "김치찌개",
            "normalized_menu_name": "김치찌개",
            "display_name": "김치찌개",
            "description": None,
            "price_text": "8,000원",
            "price_options": [],
            "ocr_confidence": 0.95,
            "locale_hint": "ko",
            "options": [],
            "residual_tokens": [],
            "risk_tag_observations": [],
        }
    ]
    signals = {"scan_quality_status": "usable", "warnings": []}
    return PrepareResponse(
        schema_version=request.schema_version,
        trace_id=request.trace_id,
        prepare_id=PREPARE_ID,
        scan_session_id=request.scan_session_id,
        prepared_items_hash=prepared_items_hash(items, signals),
        scan_quality={
            "status": "usable",
            "reasons": [],
            "retake_suggestions": [],
        },
        prepare_signals=signals,
        prepared_items=items,
        warnings=[],
        errors=[],
    )


class FakeService:
    def __init__(self) -> None:
        self.prepare_requests = []
        self.judge_requests = []

    async def prepare(self, request):
        self.prepare_requests.append(request)
        return prepare_response(request)

    async def judge(self, request):
        self.judge_requests.append(request)
        raise ContractHashMismatch(
            "prepared_items_hash_mismatch",
            "Judge request changed Prepare items or safety signals",
        )


def app(service=None) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    if service is not None:
        application.state.supervisor_service = service
    return application


async def post(application: FastAPI, path: str, payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.post(path, json=payload)


def test_prepare_endpoint_validates_and_returns_canonical_response() -> None:
    service = FakeService()
    source = prepare_request()

    response = asyncio.run(
        post(
            app(service),
            "/v1/analyze/prepare",
            source.model_dump(mode="json"),
        )
    )

    assert response.status_code == 200
    assert response.json()["prepare_id"] == str(PREPARE_ID)
    assert response.json()["prepared_items"][0]["item_id"] == "scan-123:0"
    assert service.prepare_requests == [source]


def test_judge_endpoint_returns_409_for_contract_hash_mismatch() -> None:
    service = FakeService()
    source = judge_request(count=1)

    response = asyncio.run(
        post(
            app(service),
            "/v1/analyze/judge",
            source.model_dump(mode="json"),
        )
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "prepared_items_hash_mismatch"
    assert service.judge_requests == [source]


def test_judge_endpoint_rejects_invalid_contract_before_service_call() -> None:
    service = FakeService()
    payload = judge_request(count=1).model_dump(mode="json")
    payload["store_id"] = 0

    response = asyncio.run(
        post(app(service), "/v1/analyze/judge", payload)
    )

    assert response.status_code == 422
    assert service.judge_requests == []


def test_endpoints_return_503_when_lifespan_service_is_missing() -> None:
    response = asyncio.run(
        post(
            app(),
            "/v1/analyze/prepare",
            prepare_request().model_dump(mode="json"),
        )
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "supervisor_not_configured"


def test_router_registers_only_the_two_documented_supervisor_paths() -> None:
    paths = {route.path for route in router.routes}
    assert paths == {"/v1/analyze/prepare", "/v1/analyze/judge"}
