"""Canonical ① OCR Tool request and response contracts."""

from __future__ import annotations

from pydantic import model_validator

from ._validation import ensure_unique, require_scan_context
from .common import (
    Locale,
    NodeContext,
    NodeError,
    NodeWarning,
    NonEmptyString,
    NonNegativeInt,
    Probability,
    StrictModel,
)
from .external import ImageRef, PriceOption, RiskTagObservation, ScanQuality


class OcrImageRef(ImageRef):
    image_url: str | None


class OcrScanQuality(ScanQuality):
    score: Probability


class OcrItem(StrictModel):
    item_id: NonEmptyString
    source_index: NonNegativeInt
    raw_menu_name: NonEmptyString
    description: str | None
    price_text: str | None
    price_options: list[PriceOption]
    ocr_confidence: Probability
    locale_hint: Locale | None
    risk_tag_observations: list[RiskTagObservation]


class OcrRequest(StrictModel):
    context: NodeContext
    image: OcrImageRef
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_context(self) -> "OcrRequest":
        require_scan_context(self.context, allow_store=False)
        return self


class OcrResponse(StrictModel):
    context: NodeContext
    scan_quality: OcrScanQuality
    items: list[OcrItem]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_response(self) -> "OcrResponse":
        require_scan_context(self.context, allow_store=False)
        ids = [item.item_id for item in self.items]
        indexes = [item.source_index for item in self.items]
        ensure_unique(ids, "OCR item_id values")
        ensure_unique(indexes, "OCR source_index values")
        if indexes != sorted(indexes):
            raise ValueError("OCR items must be ordered by source_index")
        for item in self.items:
            if item.item_id != f"{self.context.scan_session_id}:{item.source_index}":
                raise ValueError("OCR item_id must be {scan_session_id}:{source_index}")
        return self
