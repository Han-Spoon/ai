import json
from pathlib import Path

from ai_result.main import build_final_result
from tests.pipeline_demo_data import PIPELINE_CASES


def run_pipeline_case(case: dict) -> dict:
    result = build_final_result(case["rule_engine_input"])
    uses_gpt = bool(
        set(case["rule_engine_input"]["escalation_case"])
        & {"unknown_menu", "unknown_remain"}
    )
    return {
        "case_id": case["case_id"],
        "description": case["description"],
        "assumed_user_profile": case["assumed_user_profile"],
        "rule_engine_input": case["rule_engine_input"],
        "gpt_called": uses_gpt,
        "gpt_mode": "real" if uses_gpt else "not_used",
        "final_output": result.model_dump(mode="json"),
    }


def main() -> None:
    output_dir = Path(__file__).parent

    scenarios = [
        {
            "case_id": case["case_id"],
            "description": case["description"],
            "assumed_user_profile": case["assumed_user_profile"],
            "rule_engine_input": case["rule_engine_input"],
        }
        for case in PIPELINE_CASES
    ]
    report = {"cases": [run_pipeline_case(case) for case in PIPELINE_CASES]}

    (output_dir / "pipeline_scenarios.json").write_text(
        json.dumps({"cases": scenarios}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "pipeline_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("created tests/pipeline_scenarios.json")
    print("created tests/pipeline_results.json")


if __name__ == "__main__":
    main()
