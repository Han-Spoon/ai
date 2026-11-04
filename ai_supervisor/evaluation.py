"""Offline safety metrics and provider-free Supervisor run summaries."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Iterable, Literal, Sequence

from pydantic import StrictBool

from .contracts.common import NonEmptyString, StrictModel
from .contracts.external import JudgeResponse


class SupervisorEvaluationCase(StrictModel):
    """One reviewed user-level allergy/safety outcome."""

    case_id: NonEmptyString
    expected_risky: StrictBool
    predicted_risk_level: Literal["danger", "caution", "safe"]


class SupervisorSafetyReport(StrictModel):
    total_cases: int
    expected_risky_cases: int
    expected_safe_cases: int
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: float | None
    recall: float | None
    f2_score: float | None
    false_negative_rate: float | None
    safe_coverage: float | None
    safe_reliability: float | None
    accuracy: float | None
    predicted_risk_counts: dict[str, int]
    false_negative_case_ids: list[str]


class SupervisorRunSummary(StrictModel):
    """Ground-truth-free counters safe to emit as operational telemetry."""

    item_count: int
    normalization_status_counts: dict[str, int]
    selection_method_counts: dict[str, int]
    item_status_counts: dict[str, int]
    route_counts: dict[str, int]
    risk_level_counts: dict[str, int]
    information_status_counts: dict[str, int]
    warning_code_counts: dict[str, int]
    error_code_counts: dict[str, int]


def evaluate_supervisor_safety(
    cases: Sequence[SupervisorEvaluationCase],
) -> SupervisorSafetyReport:
    """Treat DANGER/CAUTION as positive so missed risk is the false negative."""

    true_positive = false_positive = true_negative = false_negative = 0
    false_negative_ids: list[str] = []
    predicted_counts = Counter(case.predicted_risk_level for case in cases)

    for case in cases:
        predicted_risky = case.predicted_risk_level in {"danger", "caution"}
        if case.expected_risky and predicted_risky:
            true_positive += 1
        elif not case.expected_risky and predicted_risky:
            false_positive += 1
        elif not case.expected_risky and not predicted_risky:
            true_negative += 1
        else:
            false_negative += 1
            false_negative_ids.append(case.case_id)

    total = len(cases)
    expected_risky = true_positive + false_negative
    expected_safe = true_negative + false_positive
    predicted_safe = true_negative + false_negative
    f2_denominator = 5 * true_positive + 4 * false_negative + false_positive

    return SupervisorSafetyReport(
        total_cases=total,
        expected_risky_cases=expected_risky,
        expected_safe_cases=expected_safe,
        true_positive=true_positive,
        false_positive=false_positive,
        true_negative=true_negative,
        false_negative=false_negative,
        precision=_ratio(true_positive, true_positive + false_positive),
        recall=_ratio(true_positive, expected_risky),
        f2_score=(5 * true_positive / f2_denominator if f2_denominator else None),
        false_negative_rate=_ratio(false_negative, expected_risky),
        safe_coverage=_ratio(predicted_safe, total),
        safe_reliability=_ratio(true_negative, predicted_safe),
        accuracy=_ratio(true_positive + true_negative, total),
        predicted_risk_counts={
            risk: predicted_counts[risk] for risk in ("danger", "caution", "safe")
        },
        false_negative_case_ids=false_negative_ids,
    )


def summarize_judge_response(response: JudgeResponse) -> SupervisorRunSummary:
    """Summarize routing/fallback behavior without menu text or user profiles."""

    item_warnings = [warning for item in response.items for warning in item.warnings]
    item_errors = [error for item in response.items for error in item.errors]
    warnings = [*response.warnings, *item_warnings]
    errors = [*response.errors, *item_errors]

    return SupervisorRunSummary(
        item_count=len(response.items),
        normalization_status_counts=_counts(
            item.normalization_status for item in response.items
        ),
        selection_method_counts=_counts(item.selection_method for item in response.items),
        item_status_counts=_counts(item.status for item in response.items),
        route_counts=_counts(item.route for item in response.items),
        risk_level_counts=_counts(item.risk_level for item in response.items),
        information_status_counts=_counts(
            item.information_status for item in response.items
        ),
        warning_code_counts=_counts(warning.code for warning in warnings),
        error_code_counts=_counts(error.code for error in errors),
    )


def load_supervisor_cases(path: Path) -> list[SupervisorEvaluationCase]:
    cases: list[SupervisorEvaluationCase] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
                case = SupervisorEvaluationCase.model_validate(payload)
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


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="사용자 수준 안전 정답 JSONL에서 recall/F2/FN을 계산합니다."
    )
    parser.add_argument("dataset", type=Path, help="안전 정답과 예측이 포함된 JSONL 파일")
    args = parser.parse_args(argv)
    report = evaluate_supervisor_safety(load_supervisor_cases(args.dataset))
    print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main tests
    raise SystemExit(main())
