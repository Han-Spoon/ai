from __future__ import annotations

import json

from ai_supervisor.contracts import JudgeResponse
from ai_supervisor.evaluation import (
    SupervisorEvaluationCase,
    evaluate_supervisor_safety,
    load_supervisor_cases,
    main,
    summarize_judge_response,
)
from ai_supervisor.tests.test_contracts import base_judge_response


def safety_case(case_id: str, expected_risky: bool, risk_level: str):
    return SupervisorEvaluationCase.model_validate(
        {
            "case_id": case_id,
            "expected_risky": expected_risky,
            "predicted_risk_level": risk_level,
        }
    )


def test_safety_report_prioritizes_false_negatives_and_f2() -> None:
    report = evaluate_supervisor_safety(
        [
            safety_case("tp", True, "danger"),
            safety_case("fn", True, "safe"),
            safety_case("fp", False, "caution"),
            safety_case("tn", False, "safe"),
        ]
    )

    assert report.true_positive == 1
    assert report.false_negative == 1
    assert report.false_positive == 1
    assert report.true_negative == 1
    assert report.precision == 0.5
    assert report.recall == 0.5
    assert report.f2_score == 0.5
    assert report.false_negative_rate == 0.5
    assert report.safe_coverage == 0.5
    assert report.safe_reliability == 0.5
    assert report.false_negative_case_ids == ["fn"]


def test_undefined_safety_metrics_are_not_reported_as_zero() -> None:
    report = evaluate_supervisor_safety([safety_case("safe", False, "safe")])

    assert report.precision is None
    assert report.recall is None
    assert report.f2_score is None
    assert report.safe_reliability == 1.0


def test_judge_summary_contains_no_menu_text_or_profile() -> None:
    response = JudgeResponse.model_validate(base_judge_response())
    summary = summarize_judge_response(response)

    assert summary.item_count == 1
    assert summary.normalization_status_counts == {"unmatched": 1}
    assert summary.selection_method_counts == {"none": 1}
    assert summary.route_counts == {"no_information": 1}
    assert summary.risk_level_counts == {"caution": 1}
    assert summary.warning_code_counts == {"store_context_missing": 1}
    assert "김치" not in summary.model_dump_json()


def test_jsonl_loader_and_cli_report_user_level_metrics(tmp_path, capsys) -> None:
    dataset = tmp_path / "supervisor.jsonl"
    rows = [
        safety_case("risk", True, "caution"),
        safety_case("safe", False, "safe"),
    ]
    dataset.write_text(
        "\n".join(json.dumps(row.model_dump(), ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert load_supervisor_cases(dataset) == rows
    assert main([str(dataset)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["recall"] == 1.0
    assert output["f2_score"] == 1.0
    assert output["false_negative"] == 0
