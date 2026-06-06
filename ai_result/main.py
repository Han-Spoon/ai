"""룰엔진 결과 JSON을 받아 케이스를 라우팅하고 최종 템플릿을 반환하는 엔트리포인트."""

import argparse
import json
from pathlib import Path

from ai_result.core.case_router import route_case
from ai_result.models.final_output import FinalOutput
from ai_result.models.rule_engine_input import RuleEngineInput


def build_final_result(payload: dict) -> FinalOutput:
    """룰엔진 메뉴 결과 1개를 검증한 뒤 케이스 라우터로 넘겨 최종 템플릿을 받는다."""
    rule_input = RuleEngineInput(**payload)
    return route_case(rule_input)


def build_final_results_from_judged(judged_result: dict) -> dict:
    """룰엔진 전체 결과 JSON의 각 메뉴를 케이스 라우팅해 최종 템플릿으로 교체한다."""
    result = dict(judged_result)
    result["menu_analyses"] = [
        build_final_result(menu).model_dump()
        for menu in judged_result.get("menu_analyses", [])
    ]
    return result


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="룰엔진 결과 JSON을 받아 케이스 라우팅 후 최종 템플릿을 반환합니다."
    )
    parser.add_argument(
        "--judged-json",
        required=True,
        help="이미 ai_ruleengine.analyze_all()을 거친 JSON 경로",
    )
    parser.add_argument(
        "--output",
        help="최종 ai_result JSON 저장 경로. 생략하면 화면에만 출력합니다.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="최종 JSON을 터미널에 출력합니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    final_result = build_final_results_from_judged(load_json(Path(args.judged_json)))

    if args.output:
        save_json(final_result, Path(args.output))

    if args.print_json or not args.output:
        print(json.dumps(final_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
