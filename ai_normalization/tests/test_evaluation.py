from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ai_normalization.evaluation import (
    NormalizationEvaluationCase,
    evaluate_normalization,
    load_normalization_cases,
    main,
)


def case(**updates) -> NormalizationEvaluationCase:
    payload = {
        "case_id": "case-1",
        "expected_normalized_menu_name": "김치찌개",
        "predicted_normalized_menu_name": "김치찌개",
        "expected_normalization_status": "normalized",
        "predicted_normalization_status": "normalized",
        "expected_menu_id": 101,
        "selected_menu_id": 101,
        "candidate_menu_ids": [101, 102],
        "selection_method": "deterministic",
    }
    payload.update(updates)
    return NormalizationEvaluationCase.model_validate(payload)


def test_evaluation_reports_selective_accuracy_coverage_and_confusion() -> None:
    report = evaluate_normalization(
        [
            case(),
            case(
                case_id="case-2",
                expected_menu_id=202,
                selected_menu_id=None,
                candidate_menu_ids=[202, 203],
                predicted_normalization_status="ambiguous",
                selection_method="none",
            ),
            case(
                case_id="case-3",
                expected_normalized_menu_name=None,
                predicted_normalized_menu_name="새메뉴",
                expected_normalization_status="unmatched",
                expected_menu_id=None,
                selected_menu_id=303,
                candidate_menu_ids=[303],
            ),
        ]
    )

    assert report.total_cases == 3
    assert report.name_exact_accuracy == 1.0
    assert report.candidate_recall_at_5 == 1.0
    assert report.auto_selection_precision == 0.5
    assert report.auto_selection_coverage == pytest.approx(2 / 3)
    assert report.labeled_top1_recall == 0.5
    assert report.wrong_auto_match_rate == pytest.approx(1 / 3)
    assert report.abstention_rate == pytest.approx(1 / 3)
    assert report.status_accuracy == pytest.approx(1 / 3)
    assert report.status_confusion_matrix["normalized"]["ambiguous"] == 1
    assert report.incorrect_case_ids == ["case-3"]


def test_empty_evaluation_does_not_claim_undefined_accuracy() -> None:
    report = evaluate_normalization([])

    assert report.name_exact_accuracy is None
    assert report.candidate_recall_at_5 is None
    assert report.auto_selection_precision is None
    assert report.auto_selection_coverage is None
    assert report.status_accuracy is None


def test_case_rejects_impossible_prediction_shapes() -> None:
    with pytest.raises(ValidationError):
        case(selected_menu_id=999)

    with pytest.raises(ValidationError):
        case(
            predicted_normalization_status="ambiguous",
            selected_menu_id=101,
            selection_method="none",
        )


def test_jsonl_loader_and_cli_use_no_runtime_provider(tmp_path, capsys) -> None:
    dataset = tmp_path / "normalization.jsonl"
    dataset.write_text(json.dumps(case().model_dump(), ensure_ascii=False) + "\n", encoding="utf-8")

    loaded = load_normalization_cases(dataset)
    assert loaded == [case()]
    assert main([str(dataset)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["name_exact_accuracy"] == 1.0
    assert output["auto_selection_precision"] == 1.0


def test_jsonl_loader_rejects_duplicate_case_ids(tmp_path) -> None:
    dataset = tmp_path / "duplicates.jsonl"
    row = json.dumps(case().model_dump(), ensure_ascii=False)
    dataset.write_text(f"{row}\n{row}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case_id"):
        load_normalization_cases(dataset)
