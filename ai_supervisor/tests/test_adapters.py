from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest

from ai_supervisor.adapters import LegacyOcrAdapter
from ai_supervisor.contracts.ocr import OcrRequest
from ai_supervisor.state import RunContext


TRACE_ID = UUID("0199a2f0-0000-7000-8000-000000000001")
OBSERVED_AT = datetime(2026, 10, 27, tzinfo=timezone.utc)


def request() -> OcrRequest:
    context = RunContext(
        schema_version="3.0",
        trace_id=TRACE_ID,
        scan_session_id="scan-123",
    )
    return OcrRequest(
        context=context.node_context("scan", None),
        image={
            "source": "upload",
            "storage_key": "scans/menu.jpg",
            "image_url": None,
            "version_id": None,
            "expected_etag": None,
        },
        warnings=[],
        errors=[],
    )


def test_legacy_ocr_adapter_converts_and_orders_canonical_items() -> None:
    def runner(image):
        assert image.storage_key == "scans/menu.jpg"
        return {
            "final": {
                "scan_quality": {
                    "status": "usable",
                    "score": 94,
                    "reasons": [],
                    "retake_suggestions": [],
                },
                "menu_analyses": [
                    {
                        "display_order": 2,
                        "menu_name_ko": "된장찌개",
                        "description_ko": None,
                        "price_text": "9,000원",
                        "price_options": [],
                        "is_spicy": False,
                    },
                    {
                        "display_order": 1,
                        "menu_name_ko": "김치찌개",
                        "description_ko": "돼지고기 김치찌개",
                        "price_text": "8,000원",
                        "price_options": [
                            {"name": "곱빼기", "price": 10000, "inferred": True}
                        ],
                        "ocr_confidence": 93,
                        "is_spicy": True,
                        "spicy_confidence": 78,
                    },
                ],
            }
        }

    response = asyncio.run(
        LegacyOcrAdapter(
            runner,
            observed_at_factory=lambda: OBSERVED_AT,
        ).run(request())
    )

    assert response.scan_quality.score == 0.94
    assert [item.item_id for item in response.items] == ["scan-123:0", "scan-123:1"]
    assert [item.raw_menu_name for item in response.items] == ["김치찌개", "된장찌개"]
    assert response.items[0].ocr_confidence == 0.93
    assert response.items[0].price_options[0].price_text == "10000"
    assert response.items[0].risk_tag_observations[0].confidence == 0.78
    assert response.items[0].risk_tag_observations[0].evidence.observed_at == OBSERVED_AT
    assert response.items[1].ocr_confidence == 0.0
    assert [warning.code for warning in response.warnings] == [
        "ocr_confidence_missing"
    ]


def test_legacy_ocr_adapter_rejects_out_of_range_quality_score() -> None:
    async def runner(image):
        return {
            "scan_quality": {
                "status": "usable",
                "score": 101,
                "reasons": [],
                "retake_suggestions": [],
            },
            "menu_analyses": [],
        }

    with pytest.raises(ValueError, match="between 0 and 100"):
        asyncio.run(LegacyOcrAdapter(runner).run(request()))
