"""②-A deterministic name cleaning and optional multilingual resolution."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass

from ai_supervisor.contracts.common import NodeWarning
from ai_supervisor.contracts.external import ForeignTextSegment

from ._concurrency import (
    DEFAULT_MAX_CONCURRENT_LLM_CALLS,
    validate_max_concurrent_llm_calls,
)
from .contracts import (
    CleanedMenuItem,
    NameCleaningRequest,
    NameCleaningResponse,
    NameResolutionInput,
    NameResolver,
    OcrMenuItem,
)
from .dictionaries import (
    MULTILINGUAL_ALIASES,
    NORMALIZATION_REPLACEMENTS,
    SECTION_HEADERS,
    is_beverage_item,
)
from .option_rules import extract_options

_LEADING_DECORATION_RE = re.compile(r"^[\s·•▶▷■□◆◇★☆▪▫_\-–—|※*]+")
_LEADING_CIRCLED_ORDER_RE = re.compile(r"^[①-⑳㉑-㉟㊱-㊿]\s*")
_LEADING_ORDER_RE = re.compile(r"^\d{1,3}[.)]\s+")
_LEADING_PROMOTION_RE = re.compile(
    r"^(?:best|추천)(?:\s*[:：·|\-–—]\s*|\s+)", re.IGNORECASE
)
_PRICE_NUMBER = r"(?:[\dOoO]{1,3}(?:\s*[,\.]\s*[\dOoO]{3})+|[\dOoO]{3,7})"
_TRAILING_PRICE_RE = re.compile(
    rf"(?:"
    rf"\s+(?:[₩￦$€¥]\s*)?{_PRICE_NUMBER}(?:\s*원)?"
    rf"|\s*[₩￦$€¥]\s*{_PRICE_NUMBER}(?:\s*원)?"
    rf"|\s*{_PRICE_NUMBER}\s*원"
    rf"|\s*[\(\[【]\s*(?:[₩￦$€¥]\s*)?{_PRICE_NUMBER}\s*원?\s*[\)\]】]"
    rf")\s*$",
    re.IGNORECASE,
)
_PRICE_ONLY_RE = re.compile(
    rf"^(?:[₩￦$€¥]\s*)?{_PRICE_NUMBER}(?:\s*원)?$", re.IGNORECASE
)
_PHONE_RE = re.compile(r"^(?:전화|tel\.?\s*)?\s*\d{2,4}[-\s]\d{3,4}[-\s]\d{4}$", re.IGNORECASE)
_HOURS_RE = re.compile(
    r"^(?:영업시간\s*)?(?:[01]?\d|2[0-3]):[0-5]\d\s*(?:~|-|–)\s*(?:[01]?\d|2[0-3]):[0-5]\d$"
)
_SERVING_ONLY_RE = re.compile(r"^\d+(?:\.\d+)?인분(?:추가)?$")
_NON_MENU_NOTICES = frozenset({"셀프코너이용"})
_HAS_LATIN_RE = re.compile(r"[A-Za-z]")
_HAS_FOREIGN_SCRIPT_RE = re.compile(
    r"[A-Za-z\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]"
)
_FOREIGN_TEXT_RE = re.compile(
    r"[A-Za-z]+(?:[\s_-]+[A-Za-z]+)*|[\u3040-\u30ff]+|[\u3400-\u4dbf\u4e00-\u9fff]+"
)
_LATIN_ALIAS_SEPARATOR_RE = re.compile(r"[\s_-]+")
_ORIGIN_LABELS = (
    "뉴질랜드산",
    "네덜란드산",
    "노르웨이산",
    "오스트리아산",
    "이탈리아산",
    "베트남산",
    "브라질산",
    "캐나다산",
    "러시아산",
    "스페인산",
    "프랑스산",
    "덴마크산",
    "멕시코산",
    "미국산",
    "중국산",
    "일본산",
    "태국산",
    "칠레산",
    "독일산",
    "호주산",
    "국내산",
    "국산",
)
_ORIGIN_QUALIFIERS = ("암소한우", "한우", "육우", "암소", "거세우")
_TRAILING_ORIGIN_RE = re.compile(
    rf"\s*[\(\[]?\s*(?:{'|'.join(map(re.escape, _ORIGIN_LABELS))})"
    rf"(?:\s*(?:{'|'.join(map(re.escape, _ORIGIN_QUALIFIERS))}))?\s*[\)\]]?\s*$"
)
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
    multilingual_hit: bool = False


def clean_menu_name(raw_menu_name: str) -> CleaningOutcome:
    """Apply the documented safe cleaning rules without DB or LLM access."""

    without_circled_order = _LEADING_CIRCLED_ORDER_RE.sub("", raw_menu_name)
    had_circled_order = without_circled_order != raw_menu_name
    canonical = _canonicalize_text(without_circled_order)
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
    display = _LEADING_ORDER_RE.sub("", display).strip()
    display = _LEADING_PROMOTION_RE.sub("", display).strip()
    display = _TRAILING_PRICE_RE.sub("", display).strip()
    display = _strip_trailing_origin(display)
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
    if is_beverage_item(display):
        return _invalid(display, "beverage_item")

    search_name = _compact(display).casefold()
    dictionary_hit = False
    multilingual_hit = False
    alias_key = (
        _LATIN_ALIAS_SEPARATOR_RE.sub("", search_name)
        if _HAS_LATIN_RE.search(search_name)
        else search_name
    )
    multilingual = MULTILINGUAL_ALIASES.get(alias_key)
    if multilingual is None:
        multilingual = _matching_bilingual_alias(alias_key)
    if multilingual is not None:
        search_name = multilingual
        dictionary_hit = True
        multilingual_hit = True
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
        not had_circled_order
        and canonical == display
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
        multilingual_hit=multilingual_hit,
    )


def _matching_bilingual_alias(alias_key: str) -> str | None:
    """Collapse a known Latin alias repeated by its Korean translation."""

    for latin, korean in MULTILINGUAL_ALIASES.items():
        korean_key = _compact(korean).casefold()
        if alias_key in {f"{latin}{korean_key}", f"{korean_key}{latin}"}:
            return korean
    return None


def _strip_trailing_origin(text: str) -> str:
    """Remove only a known terminal origin label; the source remains in raw_menu_name."""

    match = _TRAILING_ORIGIN_RE.search(text)
    if match is None:
        return text
    menu_name = text[: match.start()].strip()
    return menu_name if len(_compact(menu_name)) >= 2 else text


class NameCleaningAgent:
    """Batch ②-A runner. An optional resolver is used only for unresolved multilingual text."""

    def __init__(
        self,
        resolver: NameResolver | None = None,
        low_ocr_confidence_threshold: float = LOW_OCR_CONFIDENCE_THRESHOLD,
        max_concurrent_llm_calls: int = DEFAULT_MAX_CONCURRENT_LLM_CALLS,
    ) -> None:
        if not 0.0 <= low_ocr_confidence_threshold <= 1.0:
            raise ValueError("low OCR confidence threshold must be between 0 and 1")
        self._resolver = resolver
        self._low_ocr_confidence_threshold = low_ocr_confidence_threshold
        self._max_concurrent_llm_calls = validate_max_concurrent_llm_calls(
            max_concurrent_llm_calls
        )

    async def run(self, request: NameCleaningRequest) -> NameCleaningResponse:
        warnings = list(request.warnings)
        llm_slots = asyncio.Semaphore(self._max_concurrent_llm_calls)
        outcomes = await asyncio.gather(
            *(
                self._clean_item(source, llm_slots)
                for source in sorted(
                    request.items, key=lambda item: item.source_index
                )
            )
        )
        items: list[CleanedMenuItem] = []
        for item, item_warnings in outcomes:
            items.append(item)
            warnings.extend(item_warnings)

        return NameCleaningResponse(
            context=request.context,
            items=items,
            warnings=_dedupe_warnings(warnings),
            errors=list(request.errors),
        )

    async def _clean_item(
        self,
        source: OcrMenuItem,
        llm_slots: asyncio.Semaphore,
    ) -> tuple[CleanedMenuItem, list[NodeWarning]]:
        warnings: list[NodeWarning] = []
        outcome = clean_menu_name(source.raw_menu_name)
        item = _to_cleaned_item(source, outcome)
        item = item.model_copy(
            update={
                "foreign_text_segments": _deterministic_foreign_segments(
                    source, outcome
                )
            }
        )
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
                async with llm_slots:
                    resolver_input = NameResolutionInput(
                        display_name=item.display_name,
                        normalized_menu_name=item.normalized_menu_name,
                        locale_hint=item.locale_hint,
                        residual_tokens=item.residual_tokens,
                        foreign_text_segments=item.foreign_text_segments,
                    )
                    resolved = await self._resolver.resolve_name(resolver_input)
                if _valid_name_resolution(
                    item,
                    resolved.normalized_menu_name,
                    resolved.confidence,
                    resolved.foreign_text_segments,
                ):
                    semantic_details = [
                        segment.text
                        for segment in resolved.foreign_text_segments
                        if segment.role == "semantic_detail"
                    ]
                    item = item.model_copy(
                        update={
                            "normalized_menu_name": _compact(
                                resolved.normalized_menu_name
                            ).casefold(),
                            "foreign_text_segments": resolved.foreign_text_segments,
                            "residual_tokens": list(
                                dict.fromkeys(
                                    [*item.residual_tokens, *semantic_details]
                                )
                            ),
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
        return item, warnings


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
    return (
        compact in SECTION_HEADERS
        or compact in _NON_MENU_NOTICES
        or bool(
            _PHONE_RE.fullmatch(text)
            or _HOURS_RE.fullmatch(text)
            or _SERVING_ONLY_RE.fullmatch(compact)
        )
    )


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
    if item.cleaning_status == "invalid" or outcome.multilingual_hit:
        return False
    return bool(_HAS_FOREIGN_SCRIPT_RE.search(item.display_name))


def _deterministic_foreign_segments(
    source: OcrMenuItem, outcome: CleaningOutcome
) -> list[ForeignTextSegment]:
    segments = list(source.foreign_text_segments)
    if not segments:
        segments = [
            ForeignTextSegment(
                text=match.group().strip(), role="unknown", confidence=0.0
            )
            for match in _FOREIGN_TEXT_RE.finditer(source.raw_menu_name)
            if match.group().strip()
        ]
    if outcome.multilingual_hit and outcome.normalized_menu_name is not None:
        return [
            segment.model_copy(
                update={"role": "translation", "confidence": 1.0}
            )
            for segment in segments
        ]
    return segments


def _valid_name_resolution(
    item: CleanedMenuItem,
    resolved_name: str,
    confidence: float,
    resolved_segments: list[ForeignTextSegment],
) -> bool:
    compact = _compact(resolved_name).casefold()
    if confidence < 0.90 or not compact or _is_non_menu_text(compact):
        return False
    # A translation may add Korean text, but it must never delete Korean or
    # already identified ingredient tokens from the OCR name.
    source_hangul = re.findall(r"[가-힣]+", item.display_name)
    protected_tokens = [*source_hangul, *item.residual_tokens]
    if not all(token in compact for token in protected_tokens):
        return False
    if [segment.text for segment in resolved_segments] != [
        segment.text for segment in item.foreign_text_segments
    ]:
        return False
    if any(
        segment.role != "unknown" and segment.confidence < 0.90
        for segment in resolved_segments
    ):
        return False
    if any(segment.role == "unknown" for segment in resolved_segments):
        return compact == _compact(item.normalized_menu_name or "").casefold()
    return True


def _warning(code: str, item_id: str, *, forces_caution: bool) -> NodeWarning:
    messages = {
        "empty_menu_name": "메뉴명 정제 후 남은 문자열이 없습니다.",
        "price_only": "가격만 있는 문자열은 메뉴에서 제외했습니다.",
        "section_header": "메뉴판 섹션 제목은 메뉴에서 제외했습니다.",
        "invalid_menu_text": "메뉴가 아닌 안내·옵션 문자열은 메뉴에서 제외했습니다.",
        "beverage_item": "분석 대상이 아닌 음료·주류 항목은 메뉴에서 제외했습니다.",
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
