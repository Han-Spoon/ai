"""②-A deterministic name cleaning and optional multilingual resolution."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ai_supervisor.contracts.common import NodeWarning

from .contracts import (
    CleanedMenuItem,
    NameCleaningRequest,
    NameCleaningResponse,
    NameResolutionInput,
    NameResolver,
    OcrMenuItem,
)
from .dictionaries import MULTILINGUAL_ALIASES, NORMALIZATION_REPLACEMENTS, SECTION_HEADERS
from .option_rules import extract_options

_LEADING_DECORATION_RE = re.compile(r"^[\s·•▶▷■□◆◇★☆▪▫_\-–—|※*]+")
_TRAILING_PRICE_RE = re.compile(
    r"(?:^|\s)(?:[₩￦$€¥]\s*)?(?:\d{1,3}(?:,\d{3})+|\d{3,7})(?:\s*원)?\s*$"
)
_PRICE_ONLY_RE = re.compile(
    r"^(?:[₩￦$€¥]\s*)?(?:\d{1,3}(?:,\d{3})+|\d{3,7})(?:\s*원)?$"
)
_PHONE_RE = re.compile(r"^(?:전화|tel\.?\s*)?\s*\d{2,4}[-\s]\d{3,4}[-\s]\d{4}$", re.IGNORECASE)
_HOURS_RE = re.compile(
    r"^(?:영업시간\s*)?(?:[01]?\d|2[0-3]):[0-5]\d\s*(?:~|-|–)\s*(?:[01]?\d|2[0-3]):[0-5]\d$"
)
_HAS_LATIN_RE = re.compile(r"[A-Za-z]")
LOW_OCR_CONFIDENCE_THRESHOLD = 0.65


@dataclass(frozen=True)
class CleaningOutcome:
    display_name: str
    normalized_menu_name: str | None
    options: tuple[str, ...]
    residual_tokens: tuple[str, ...]
    status: str
    warning_code: str | None = None
    dictionary_hit: bool = False


def clean_menu_name(raw_menu_name: str) -> CleaningOutcome:
    """Apply the documented safe cleaning rules without DB or LLM access."""

    canonical = _canonicalize_text(raw_menu_name)
    if not canonical:
        return _invalid("", "empty_menu_name")
    if _PRICE_ONLY_RE.fullmatch(canonical):
        return _invalid("", "price_only")
    if _is_non_menu_text(canonical):
        warning = (
            "section_header"
            if _compact(canonical) in SECTION_HEADERS
            else "invalid_menu_text"
        )
        return _invalid(canonical, warning)

    display = _LEADING_DECORATION_RE.sub("", canonical).strip()
    display = _TRAILING_PRICE_RE.sub("", display).strip()
    if not display:
        warning = "price_only" if _PRICE_ONLY_RE.fullmatch(canonical) else "empty_menu_name"
        return _invalid("", warning)

    extraction = extract_options(display)
    display = extraction.menu_text.strip()
    if not display:
        return _invalid("", "empty_menu_name")
    if _is_non_menu_text(display):
        warning = (
            "section_header"
            if _compact(display) in SECTION_HEADERS
            else "invalid_menu_text"
        )
        return _invalid(display, warning)

    search_name = _compact(display).casefold()
    dictionary_hit = False
    multilingual = MULTILINGUAL_ALIASES.get(search_name)
    if multilingual is not None:
        search_name = multilingual
        dictionary_hit = True
    else:
        for source, target in sorted(
            NORMALIZATION_REPLACEMENTS.items(), key=lambda entry: len(entry[0]), reverse=True
        ):
            if source in search_name:
                search_name = search_name.replace(source, target)
                dictionary_hit = True

    if not search_name:
        return _invalid(display, "empty_menu_name")

    unchanged = (
        canonical == display
        and search_name == canonical
        and not extraction.options
        and not dictionary_hit
    )
    return CleaningOutcome(
        display_name=display,
        normalized_menu_name=search_name,
        options=extraction.options,
        residual_tokens=extraction.residual_tokens,
        status="unchanged" if unchanged else "cleaned",
        dictionary_hit=dictionary_hit,
    )


class NameCleaningAgent:
    """Batch ②-A runner. An optional resolver is used only for unresolved multilingual text."""

    def __init__(
        self,
        resolver: NameResolver | None = None,
        low_ocr_confidence_threshold: float = LOW_OCR_CONFIDENCE_THRESHOLD,
    ) -> None:
        if not 0.0 <= low_ocr_confidence_threshold <= 1.0:
            raise ValueError("low OCR confidence threshold must be between 0 and 1")
        self._resolver = resolver
        self._low_ocr_confidence_threshold = low_ocr_confidence_threshold

    async def run(self, request: NameCleaningRequest) -> NameCleaningResponse:
        items: list[CleanedMenuItem] = []
        warnings = list(request.warnings)

        for source in sorted(request.items, key=lambda item: item.source_index):
            outcome = clean_menu_name(source.raw_menu_name)
            item = _to_cleaned_item(source, outcome)
            if outcome.warning_code is not None:
                warnings.append(
                    _warning(outcome.warning_code, source.item_id, forces_caution=False)
                )
            if (
                item.cleaning_status != "invalid"
                and source.ocr_confidence < self._low_ocr_confidence_threshold
            ):
                warnings.append(
                    _warning("low_ocr_confidence", source.item_id, forces_caution=True)
                )

            if self._resolver is not None and _needs_multilingual_resolution(item, outcome):
                try:
                    resolver_input = NameResolutionInput(
                        display_name=item.display_name,
                        normalized_menu_name=item.normalized_menu_name,
                        locale_hint=item.locale_hint,
                        residual_tokens=item.residual_tokens,
                    )
                    resolved = await self._resolver.resolve_name(resolver_input)
                    if _valid_name_resolution(
                        item, resolved.normalized_menu_name, resolved.confidence
                    ):
                        item = item.model_copy(
                            update={
                                "normalized_menu_name": _compact(
                                    resolved.normalized_menu_name
                                ).casefold(),
                                "cleaning_status": "cleaned",
                            }
                        )
                    else:
                        warnings.append(
                            _warning("name_llm_failed", source.item_id, forces_caution=False)
                        )
                except Exception:
                    warnings.append(
                        _warning("name_llm_failed", source.item_id, forces_caution=False)
                    )
            items.append(item)

        return NameCleaningResponse(
            context=request.context,
            items=items,
            warnings=_dedupe_warnings(warnings),
            errors=list(request.errors),
        )


def _canonicalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    without_controls = "".join(
        " " if unicodedata.category(character) == "Cc" else character
        for character in normalized
    )
    return re.sub(r"\s+", " ", without_controls).strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _is_non_menu_text(text: str) -> bool:
    compact = _compact(text).casefold()
    return compact in SECTION_HEADERS or bool(_PHONE_RE.fullmatch(text) or _HOURS_RE.fullmatch(text))


def _invalid(display_name: str, warning_code: str) -> CleaningOutcome:
    return CleaningOutcome(
        display_name=display_name,
        normalized_menu_name=None,
        options=(),
        residual_tokens=(),
        status="invalid",
        warning_code=warning_code,
    )


def _to_cleaned_item(source: OcrMenuItem, outcome: CleaningOutcome) -> CleanedMenuItem:
    return CleanedMenuItem(
        **source.model_dump(),
        normalized_menu_name=outcome.normalized_menu_name,
        display_name=outcome.display_name,
        options=list(outcome.options),
        residual_tokens=list(outcome.residual_tokens),
        cleaning_status=outcome.status,
    )


def _needs_multilingual_resolution(item: CleanedMenuItem, outcome: CleaningOutcome) -> bool:
    if item.cleaning_status == "invalid" or outcome.dictionary_hit:
        return False
    return bool(_HAS_LATIN_RE.search(item.display_name))


def _valid_name_resolution(
    item: CleanedMenuItem, resolved_name: str, confidence: float
) -> bool:
    compact = _compact(resolved_name).casefold()
    if confidence < 0.90 or not compact or _is_non_menu_text(compact):
        return False
    # A translation may add Korean text, but it must never delete Korean or
    # already identified ingredient tokens from the OCR name.
    source_hangul = re.findall(r"[가-힣]+", item.display_name)
    protected_tokens = [*source_hangul, *item.residual_tokens]
    return all(token in compact for token in protected_tokens)


def _warning(code: str, item_id: str, *, forces_caution: bool) -> NodeWarning:
    messages = {
        "empty_menu_name": "메뉴명 정제 후 남은 문자열이 없습니다.",
        "price_only": "가격만 있는 문자열은 메뉴에서 제외했습니다.",
        "section_header": "메뉴판 섹션 제목은 메뉴에서 제외했습니다.",
        "invalid_menu_text": "전화번호 또는 영업시간 문자열은 메뉴에서 제외했습니다.",
        "name_llm_failed": "다국어 메뉴명 해석에 실패해 규칙 기반 이름을 유지했습니다.",
        "low_ocr_confidence": "OCR 신뢰도가 설정된 품질 기준보다 낮습니다.",
    }
    return NodeWarning(
        code=code,
        node="normalization",
        item_id=item_id,
        message=messages[code],
        forces_caution=forces_caution,
    )


def _dedupe_warnings(warnings: list[NodeWarning]) -> list[NodeWarning]:
    result: list[NodeWarning] = []
    seen: set[tuple[str, str, str | None]] = set()
    for warning in warnings:
        key = (warning.code, warning.node, warning.item_id)
        if key not in seen:
            result.append(warning)
            seen.add(key)
    return result
