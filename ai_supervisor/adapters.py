"""Interfaces and boundary adapters for existing ①~⑧ implementations."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Protocol, TypeVar, Union

from .contracts.common import EvidenceRef, NodeWarning
from .contracts.external import PriceOption, RiskTagObservation
from .contracts.ocr import OcrItem, OcrRequest, OcrResponse, OcrScanQuality

RequestT = TypeVar("RequestT", contravariant=True)
ResponseT = TypeVar("ResponseT", covariant=True)


class NodeAdapter(Protocol[RequestT, ResponseT]):
    async def run(self, request: RequestT) -> ResponseT:
        """Run one node without calling any downstream node."""
        ...


LegacyOcrResult = Mapping[str, Any]
LegacyOcrRunner = Callable[
    [Any], Union[LegacyOcrResult, Awaitable[LegacyOcrResult]]
]


class LegacyOcrAdapter:
    """Convert the existing OCR result dictionary into the canonical ① contract.

    Image retrieval and the actual CLOVA call stay in the injected runner so this
    adapter neither reads storage nor creates external clients per request.
    """

    def __init__(
        self,
        runner: LegacyOcrRunner,
        *,
        observed_at_factory: Callable[[], datetime] = lambda: datetime.now(
            timezone.utc
        ),
    ) -> None:
        self._runner = runner
        self._observed_at_factory = observed_at_factory

    async def run(self, request: OcrRequest) -> OcrResponse:
        if inspect.iscoroutinefunction(self._runner):
            raw = self._runner(request.image)
        else:
            raw = await asyncio.to_thread(self._runner, request.image)
        result = await raw if inspect.isawaitable(raw) else raw
        if not isinstance(result, Mapping):
            raise TypeError("legacy OCR runner must return an object")
        final = result.get("final", result)
        if not isinstance(final, Mapping):
            raise ValueError("legacy OCR result.final must be an object")

        quality = _legacy_scan_quality(final.get("scan_quality"))
        menu_values = final.get("menu_analyses", [])
        if not isinstance(menu_values, list):
            raise ValueError("legacy OCR menu_analyses must be an array")
        menus = sorted(menu_values, key=_legacy_display_order)
        warnings = list(request.warnings)
        items: list[OcrItem] = []
        for source_index, value in enumerate(menus):
            if not isinstance(value, Mapping):
                raise ValueError("legacy OCR menu item must be an object")
            item, item_warnings = _legacy_ocr_item(
                request,
                value,
                source_index,
                self._observed_at_factory,
            )
            items.append(item)
            warnings.extend(item_warnings)

        return OcrResponse(
            context=request.context,
            scan_quality=quality,
            items=items,
            warnings=_dedupe_warnings(warnings),
            errors=list(request.errors),
        )


def _legacy_scan_quality(value: Any) -> OcrScanQuality:
    if not isinstance(value, Mapping):
        raise ValueError("legacy OCR scan_quality must be an object")
    score = value.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("legacy OCR scan_quality.score must be numeric")
    normalized_score = float(score) / 100.0 if score > 1 else float(score)
    if not 0.0 <= normalized_score <= 1.0:
        raise ValueError("legacy OCR scan_quality.score must be between 0 and 100")
    return OcrScanQuality(
        status=value.get("status"),
        score=normalized_score,
        reasons=_string_list(value.get("reasons", []), "scan_quality.reasons"),
        retake_suggestions=_string_list(
            value.get("retake_suggestions", []),
            "scan_quality.retake_suggestions",
        ),
    )


def _legacy_ocr_item(
    request: OcrRequest,
    value: Mapping[str, Any],
    source_index: int,
    observed_at_factory: Callable[[], datetime],
) -> tuple[OcrItem, list[NodeWarning]]:
    item_id = f"{request.context.scan_session_id}:{source_index}"
    confidence = value.get("ocr_confidence", value.get("confidence"))
    warnings: list[NodeWarning] = []
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0
        warnings.append(
            NodeWarning(
                code="ocr_confidence_missing",
                node="ocr_adapter",
                item_id=item_id,
                message="메뉴별 OCR 신뢰도가 없어 보수적으로 처리합니다.",
                forces_caution=True,
            )
        )
    normalized_confidence = (
        float(confidence) / 100.0 if confidence > 1 else float(confidence)
    )
    if not 0.0 <= normalized_confidence <= 1.0:
        raise ValueError("legacy OCR item confidence must be between 0 and 100")

    raw_menu_name = value.get("menu_name_ko")
    if not isinstance(raw_menu_name, str) or not raw_menu_name.strip():
        raise ValueError("legacy OCR item menu_name_ko must not be empty")

    observations: list[RiskTagObservation] = []
    if value.get("is_spicy") is True:
        spicy_confidence = value.get("spicy_confidence", 1.0)
        if isinstance(spicy_confidence, bool) or not isinstance(
            spicy_confidence, (int, float)
        ):
            raise ValueError("legacy OCR spicy_confidence must be numeric")
        normalized_spicy_confidence = (
            float(spicy_confidence) / 100.0
            if spicy_confidence > 1
            else float(spicy_confidence)
        )
        if not 0.0 <= normalized_spicy_confidence <= 1.0:
            raise ValueError("legacy OCR spicy_confidence must be between 0 and 100")
        observations.append(
            RiskTagObservation(
                tag="is_spicy",
                confidence=normalized_spicy_confidence,
                evidence=EvidenceRef(
                    evidence_id=f"ocr:{item_id}:is_spicy",
                    evidence_class="soft",
                    source_type="ocr_text_hint",
                    source_ref=item_id,
                    verification_status="inferred",
                    reliability_weight=0.4,
                    observed_at=observed_at_factory(),
                ),
            )
        )

    return (
        OcrItem(
            item_id=item_id,
            source_index=source_index,
            raw_menu_name=raw_menu_name.strip(),
            description=_optional_string(value.get("description_ko")),
            price_text=_optional_string(value.get("price_text")),
            price_options=_legacy_price_options(value.get("price_options", [])),
            ocr_confidence=normalized_confidence,
            locale_hint="ko",
            risk_tag_observations=observations,
        ),
        warnings,
    )


def _legacy_price_options(value: Any) -> list[PriceOption]:
    if not isinstance(value, list):
        raise ValueError("legacy OCR price_options must be an array")
    options: list[PriceOption] = []
    for option in value:
        if not isinstance(option, Mapping):
            raise ValueError("legacy OCR price option must be an object")
        label = option.get("label", option.get("name"))
        price = option.get("price_text", option.get("price"))
        if not isinstance(label, str) or not label.strip() or price is None:
            raise ValueError("legacy OCR price option requires label and price")
        options.append(
            PriceOption(
                label=label.strip(),
                price_text=str(price),
                inferred=option.get("inferred", False) is True,
            )
        )
    return options


def _legacy_display_order(value: Any) -> tuple[int, int]:
    if not isinstance(value, Mapping):
        return (1, 0)
    order = value.get("display_order")
    if isinstance(order, int) and not isinstance(order, bool):
        return (0, order)
    return (1, 0)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    return stripped or None


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"legacy OCR {field} must be an array of strings")
    return value


def _dedupe_warnings(warnings: list[NodeWarning]) -> list[NodeWarning]:
    result: list[NodeWarning] = []
    seen: set[tuple[str, str, str | None]] = set()
    for warning in warnings:
        key = (warning.code, warning.node, warning.item_id)
        if key not in seen:
            result.append(warning)
            seen.add(key)
    return result
