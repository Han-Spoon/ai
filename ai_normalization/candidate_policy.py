"""②-B candidate policy with an optional, tightly validated LLM resolver."""

from __future__ import annotations

from dataclasses import dataclass

from ai_supervisor.contracts.common import NodeWarning

from .contracts import (
    CandidateResolver,
    CandidateResolutionCandidate,
    CandidateResolutionInput,
    CandidateSelectionItem,
    CandidateSelectionRequest,
    CandidateSelectionResponse,
    CandidateSelectionResult,
)


@dataclass(frozen=True)
class CandidatePolicy:
    candidate_floor: float = 0.65
    accept_threshold: float = 0.92
    ambiguity_margin: float = 0.08
    llm_accept_confidence: float = 0.90

    def __post_init__(self) -> None:
        values = (
            self.candidate_floor,
            self.accept_threshold,
            self.ambiguity_margin,
            self.llm_accept_confidence,
        )
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("candidate policy values must be between 0 and 1")
        if self.candidate_floor > self.accept_threshold:
            raise ValueError("candidate_floor must not exceed accept_threshold")


class CandidateSelectionAgent:
    def __init__(
        self,
        resolver: CandidateResolver | None = None,
        policy: CandidatePolicy | None = None,
    ) -> None:
        self._resolver = resolver
        self._policy = policy or CandidatePolicy()

    async def run(self, request: CandidateSelectionRequest) -> CandidateSelectionResponse:
        results: list[CandidateSelectionResult] = []
        warnings = list(request.warnings)
        for item in sorted(request.items, key=lambda value: value.source_index):
            result, item_warnings = await self._select(item)
            results.append(result)
            warnings.extend(item_warnings)
        return CandidateSelectionResponse(
            context=request.context,
            items=results,
            warnings=_dedupe_warnings(warnings),
            errors=list(request.errors),
        )

    async def _select(
        self, item: CandidateSelectionItem
    ) -> tuple[CandidateSelectionResult, list[NodeWarning]]:
        if not item.normalized_menu_name:
            return _result(item, "invalid", "none", ["invalid_input"]), []
        if item.candidate_status == "unavailable":
            warning = _warning(
                "candidate_unavailable",
                item.item_id,
                "메뉴 후보를 조회하지 못해 자동 확정을 중단했습니다.",
            )
            return _result(item, "unmatched", "none", ["candidate_unavailable"]), [warning]
        if item.candidate_status == "not_found":
            return _result(item, "unmatched", "none", ["candidate_not_found"]), []

        top = item.match_candidates[0]
        margin = (
            1.0
            if len(item.match_candidates) == 1
            else top.score - item.match_candidates[1].score
        )
        if top.score < self._policy.candidate_floor:
            return _result(item, "unmatched", "none", ["below_accept_threshold"]), []
        if (
            top.score >= self._policy.accept_threshold
            and margin >= self._policy.ambiguity_margin
        ):
            reason = (
                "exact_unique"
                if len(item.match_candidates) == 1 and top.match_type != "fuzzy"
                else "score_and_margin"
            )
            return _result(
                item,
                "normalized",
                "deterministic",
                [reason],
                selected_candidate=top,
            ), []

        base_reason = (
            "multiple_close_candidates"
            if len(item.match_candidates) > 1 and margin < self._policy.ambiguity_margin
            else "below_accept_threshold"
        )
        warnings: list[NodeWarning] = []

        if self._resolver is None or not _has_llm_context(item):
            warnings.extend(_ambiguity_warnings(item, base_reason))
            return _result(item, "ambiguous", "none", [base_reason]), warnings

        try:
            resolver_input = CandidateResolutionInput(
                raw_menu_name=item.raw_menu_name,
                normalized_menu_name=item.normalized_menu_name,
                display_name=item.display_name,
                description=item.description,
                locale_hint=item.locale_hint,
                options=item.options,
                residual_tokens=item.residual_tokens,
                candidates=[
                    CandidateResolutionCandidate(
                        menu_id=candidate.menu_id,
                        canonical_name=candidate.canonical_name,
                    )
                    for candidate in item.match_candidates
                ],
            )
            resolution = await self._resolver.resolve_candidate(resolver_input)
        except Exception:
            warnings.extend(_ambiguity_warnings(item, base_reason))
            warnings.append(
                _warning(
                    "candidate_llm_failed",
                    item.item_id,
                    "후보 문맥 해석에 실패해 규칙 기반 모호 상태를 유지했습니다.",
                )
            )
            return _result(item, "ambiguous", "none", [base_reason, "llm_failed"]), warnings

        selected = next(
            (
                candidate
                for candidate in item.match_candidates
                if candidate.menu_id == resolution.selected_menu_id
            ),
            None,
        )
        if selected is None:
            warnings.extend(_ambiguity_warnings(item, base_reason))
            warnings.append(
                _warning(
                    "invalid_llm_candidate",
                    item.item_id,
                    "허용 후보 밖의 메뉴를 선택한 결과를 폐기했습니다.",
                )
            )
            return _result(item, "ambiguous", "none", [base_reason, "llm_failed"]), warnings
        if not _resolution_has_context(item, resolution.reason_code):
            warnings.extend(_ambiguity_warnings(item, base_reason))
            warnings.append(
                _warning(
                    "invalid_llm_candidate",
                    item.item_id,
                    "입력 문맥으로 뒷받침되지 않는 후보 선택 결과를 폐기했습니다.",
                )
            )
            return _result(item, "ambiguous", "none", [base_reason, "llm_failed"]), warnings
        if resolution.confidence < self._policy.llm_accept_confidence:
            warnings.extend(_ambiguity_warnings(item, base_reason))
            return _result(item, "ambiguous", "none", [base_reason]), warnings

        return _result(
            item,
            "normalized",
            "llm",
            ["llm_context_resolved"],
            selected_candidate=selected,
        ), warnings


def _result(
    item: CandidateSelectionItem,
    status: str,
    method: str,
    reasons: list[str],
    *,
    selected_candidate=None,
) -> CandidateSelectionResult:
    return CandidateSelectionResult(
        **item.model_dump(),
        normalization_status=status,
        selection_method=method,
        selection_reason_codes=reasons,
        selected_candidate=selected_candidate,
    )


def _has_llm_context(item: CandidateSelectionItem) -> bool:
    return bool(
        (item.description and item.description.strip())
        or item.locale_hint
        or item.options
        or item.residual_tokens
    )


def _resolution_has_context(item: CandidateSelectionItem, reason_code: str) -> bool:
    if reason_code == "description_supports_candidate":
        return bool(item.description and item.description.strip())
    if reason_code == "locale_supports_candidate":
        return item.locale_hint is not None
    if reason_code == "option_supports_candidate":
        return bool(item.options or item.residual_tokens)
    return False


def _ambiguity_warnings(
    item: CandidateSelectionItem, base_reason: str
) -> list[NodeWarning]:
    if base_reason != "multiple_close_candidates":
        return []
    return [
        _warning(
            base_reason,
            item.item_id,
            "점수가 가까운 메뉴 후보가 여러 개라 자동 확정하지 않았습니다.",
        )
    ]


def _warning(code: str, item_id: str, message: str) -> NodeWarning:
    return NodeWarning(
        code=code,
        node="normalization",
        item_id=item_id,
        message=message,
        forces_caution=True,
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
