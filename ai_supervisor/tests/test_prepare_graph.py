from __future__ import annotations

import asyncio
from uuid import UUID

from ai_normalization import NameCleaningAgent
from ai_supervisor.contracts.external import ImageRef
from ai_supervisor.contracts.ocr import (
    OcrItem,
    OcrResponse,
    OcrScanQuality,
)
from ai_supervisor.hashing import validate_prepare_response_hash
from ai_supervisor.prepare_graph import PrepareDependencies, build_prepare_graph
from ai_supervisor.state import RunContext

TRACE_ID = UUID("0199a2f0-0000-7000-8000-000000000001")
PREPARE_ID = UUID("0199a2f0-0000-7000-8000-000000000101")


class FakeOcr:
    def __init__(self, status: str, names: list[str]) -> None:
        self.status = status
        self.names = names
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return OcrResponse(
            context=request.context,
            scan_quality=OcrScanQuality(
                status=self.status,
                score=0.4 if self.status != "usable" else 0.95,
                reasons=[] if self.status == "usable" else ["사진 품질이 낮습니다."],
                retake_suggestions=(
                    ["메뉴판을 정면에서 다시 촬영해주세요."]
                    if self.status == "needs_retake"
                    else []
                ),
            ),
            items=[
                OcrItem(
                    item_id=f"scan-123:{index}",
                    source_index=index,
                    raw_menu_name=name,
                    description=None,
                    price_text="8,000원",
                    price_options=[],
                    ocr_confidence=0.93,
                    locale_hint="ko",
                    risk_tag_observations=[],
                )
                for index, name in enumerate(self.names)
            ],
            warnings=[],
            errors=[],
        )


class FailingNormalization:
    async def run(self, request):
        raise AssertionError("normalization must not run for needs_retake")


def context() -> RunContext:
    return RunContext(
        schema_version="3.0",
        trace_id=TRACE_ID,
        scan_session_id="scan-123",
    )


def input_state() -> dict:
    return {
        "image": ImageRef(
            source="camera",
            storage_key="scans/menu.jpg",
            version_id=None,
            expected_etag=None,
        ),
        "locale": "ko",
        "warnings": [],
        "errors": [],
    }


def test_prepare_graph_runs_ocr_then_name_cleaning() -> None:
    ocr = FakeOcr("usable", ["■ 김치 찌개 2인"])
    graph = build_prepare_graph(
        PrepareDependencies(
            ocr=ocr,
            normalization=NameCleaningAgent(),
            prepare_id_factory=lambda: PREPARE_ID,
        )
    )

    result = asyncio.run(graph.ainvoke(input_state(), context=context()))
    response = result["response"]

    assert response.prepare_id == PREPARE_ID
    assert response.prepared_items[0].item_id == "scan-123:0"
    assert response.prepared_items[0].display_name == "김치 찌개"
    assert response.prepared_items[0].normalized_menu_name == "김치찌개"
    assert response.prepared_items[0].options == ["2인"]
    assert "store_id" not in ocr.requests[0].context.model_fields_set
    validate_prepare_response_hash(response)


def test_prepare_graph_skips_normalization_for_needs_retake() -> None:
    graph = build_prepare_graph(
        PrepareDependencies(
            ocr=FakeOcr("needs_retake", ["흐린 메뉴"]),
            normalization=FailingNormalization(),
            prepare_id_factory=lambda: PREPARE_ID,
        )
    )

    result = asyncio.run(graph.ainvoke(input_state(), context=context()))
    response = result["response"]

    assert response.scan_quality.status == "needs_retake"
    assert response.prepared_items == []
    validate_prepare_response_hash(response)


def test_prepare_graph_preserves_low_quality_as_caution_signal() -> None:
    graph = build_prepare_graph(
        PrepareDependencies(
            ocr=FakeOcr("low_confidence", ["된장찌개"]),
            normalization=NameCleaningAgent(),
            prepare_id_factory=lambda: PREPARE_ID,
        )
    )

    result = asyncio.run(graph.ainvoke(input_state(), context=context()))
    response = result["response"]

    assert response.prepared_items[0].display_name == "된장찌개"
    assert [warning.code for warning in response.prepare_signals.warnings] == [
        "scan_quality_low_confidence"
    ]
    assert response.prepare_signals.warnings[0].forces_caution is True
    validate_prepare_response_hash(response)


def test_prepare_graph_filters_invalid_items_and_blocks_empty_result() -> None:
    graph = build_prepare_graph(
        PrepareDependencies(
            ocr=FakeOcr("usable", ["식사류"]),
            normalization=NameCleaningAgent(),
            prepare_id_factory=lambda: PREPARE_ID,
        )
    )

    result = asyncio.run(graph.ainvoke(input_state(), context=context()))
    response = result["response"]

    assert response.prepared_items == []
    assert any(warning.code == "section_header" for warning in response.warnings)
    assert [error.code for error in response.errors] == ["no_menu_items"]
    validate_prepare_response_hash(response)


def test_prepare_graph_compiles_with_documented_nodes() -> None:
    graph = build_prepare_graph(
        PrepareDependencies(
            ocr=FakeOcr("usable", []),
            normalization=NameCleaningAgent(),
            prepare_id_factory=lambda: PREPARE_ID,
        )
    )

    assert {"ocr", "clean_names", "finalize_retake", "finalize_prepared"} <= set(
        graph.get_graph().nodes
    )
