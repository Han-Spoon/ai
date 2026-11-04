"""Offline, API-free evaluation for Menu Normalization.

The validation dataset location is intentionally not fixed here.  Teams can feed
any reviewed JSONL dataset to ``python -m ai_normalization.evaluation`` without
changing the runtime Normalization contracts or calling an LLM.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Sequence

from pydantic import field_validator, model_validator

from ai_supervisor.contracts.common import NonEmptyString, PositiveInt, StrictModel

from .contracts import NormalizationStatus, SelectionMethod

_STATUSES: tuple[NormalizationStatus, ...] = (
    "normalized",
    "ambiguous",
    "unmatched",
    "invalid",
)
_METHODS: tuple[SelectionMethod, ...] = ("deterministic", "llm", "none")


class NormalizationEvaluationCase(StrictModel):
    """One human-reviewed prediction used only for offline evaluation."""

    case_id: NonEmptyString
    expected_normalized_menu_name: str | None
    predicted_normalized_menu_name: str | None
    expected_normalization_status: NormalizationStatus
    predicted_normalization_status: NormalizationStatus
    expected_menu_id: PositiveInt | None
    selected_menu_id: PositiveInt | None
    candidate_menu_ids: list[PositiveInt]
    selection_method: SelectionMethod

    @field_validator("candidate_menu_ids")
    @classmethod
    def candidate_ids_must_be_unique(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("candidate_menu_ids must be unique")
        if len(value) > 5:
            raise ValueError("candidate_menu_ids must contain at most five IDs")
        return value

    @model_validator(mode="after")
    def validate_prediction_shape(self) -> "NormalizationEvaluationCase":
        if self.predicted_normalization_status == "normalized":
            if self.selected_menu_id is None:
                raise ValueError("normalized predictions require selected_menu_id")
            if self.selection_method == "none":
                raise ValueError("normalized predictions require a selection method")
            if self.selected_menu_id not in self.candidate_menu_ids:
                raise ValueError("selected_menu_id must be one of candidate_menu_ids")
        elif self.selected_menu_id is not None or self.selection_method != "none":
            raise ValueError(
                "non-normalized predictions require selected_menu_id null and method none"
            )
        return self


class NormalizationEvaluationReport(StrictModel):
    total_cases: int
    name_labeled_cases: int
    exact_name_matches: int
    name_exact_accuracy: float | None
    menu_labeled_cases: int
    candidate_hits: int
    candidate_recall_at_5: float | None
    auto_selected_cases: int
    correct_auto_selections: int
    incorrect_auto_selections: int
    auto_selection_precision: float | None
    auto_selection_coverage: float | None
    labeled_top1_recall: float | None
    wrong_auto_match_rate: float | None
    abstention_rate: float | None
    status_accuracy: float | None
    predicted_status_counts: dict[str, int]
    selection_method_counts: dict[str, int]
    status_confusion_matrix: dict[str, dict[str, int]]
    incorrect_case_ids: list[str]


def evaluate_normalization(
    cases: Sequence[NormalizationEvaluationCase],
) -> NormalizationEvaluationReport:
    """Calculate selective-classification metrics without any provider calls."""

    total = len(cases)
    name_labeled = [
        case for case in cases if case.expected_normalized_menu_name is not None
    ]
    exact_name_matches = sum(
        case.predicted_normalized_menu_name == case.expected_normalized_menu_name
        for case in name_labeled
    )

    menu_labeled = [case for case in cases if case.expected_menu_id is not None]
    candidate_hits = sum(
        case.expected_menu_id in case.candidate_menu_ids for case in menu_labeled
    )
    auto_selected = [case for case in cases if case.selected_menu_id is not None]
    correct_auto = [
        case
        for case in auto_selected
        if case.expected_menu_id is not None
        and case.selected_menu_id == case.expected_menu_id
    ]
    incorrect_auto = [
        case
        for case in auto_selected
        if case.expected_menu_id is None
        or case.selected_menu_id != case.expected_menu_id
    ]

    status_counts = Counter(case.predicted_normalization_status for case in cases)
    method_counts = Counter(case.selection_method for case in cases)
    confusion = {
        expected: {
            predicted: sum(
                case.expected_normalization_status == expected
                and case.predicted_normalization_status == predicted
                for case in cases
            )
            for predicted in _STATUSES
        }
        for expected in _STATUSES
    }
    correct_statuses = sum(
        case.expected_normalization_status == case.predicted_normalization_status
        for case in cases
    )

    return NormalizationEvaluationReport(
        total_cases=total,
        name_labeled_cases=len(name_labeled),
        exact_name_matches=exact_name_matches,
        name_exact_accuracy=_ratio(exact_name_matches, len(name_labeled)),
        menu_labeled_cases=len(menu_labeled),
        candidate_hits=candidate_hits,
        candidate_recall_at_5=_ratio(candidate_hits, len(menu_labeled)),
        auto_selected_cases=len(auto_selected),
        correct_auto_selections=len(correct_auto),
        incorrect_auto_selections=len(incorrect_auto),
        auto_selection_precision=_ratio(len(correct_auto), len(auto_selected)),
        auto_selection_coverage=_ratio(len(auto_selected), total),
        labeled_top1_recall=_ratio(len(correct_auto), len(menu_labeled)),
        wrong_auto_match_rate=_ratio(len(incorrect_auto), total),
        abstention_rate=_ratio(total - len(auto_selected), total),
        status_accuracy=_ratio(correct_statuses, total),
        predicted_status_counts={status: status_counts[status] for status in _STATUSES},
        selection_method_counts={method: method_counts[method] for method in _METHODS},
        status_confusion_matrix=confusion,
        incorrect_case_ids=[case.case_id for case in incorrect_auto],
    )


def load_normalization_cases(path: Path) -> list[NormalizationEvaluationCase]:
    """Load strict JSONL rows and report the exact line of invalid labels."""

    cases: list[NormalizationEvaluationCase] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
                case = NormalizationEvaluationCase.model_validate(payload)
            except (json.JSONDecodeError, ValueError) as error:
                raise ValueError(
                    f"invalid evaluation row at {path}:{line_number}: {error}"
                ) from error
            if case.case_id in seen:
                raise ValueError(
                    f"duplicate case_id at {path}:{line_number}: {case.case_id}"
                )
            seen.add(case.case_id)
            cases.append(case)
    return cases


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="고정 정답 JSONL로 메뉴 정규화 지표를 계산합니다 (API 호출 없음)."
    )
    parser.add_argument("dataset", type=Path, help="정답과 예측이 포함된 JSONL 파일")
    args = parser.parse_args(argv)
    report = evaluate_normalization(load_normalization_cases(args.dataset))
    print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main tests
    raise SystemExit(main())
