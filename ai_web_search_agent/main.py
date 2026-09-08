from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import WebSearchAgent  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="한스푼 Web Search Agent")
    parser.add_argument("--judged-json", help="Rule Engine 결과 JSON 경로")
    parser.add_argument("--ocr-json", help="OCR 결과 JSON 경로. --profile 과 함께 쓰면 Rule Engine을 먼저 실행")
    parser.add_argument("--profile", help="사용자 프로필 JSON 경로. --ocr-json 과 함께 필요")
    parser.add_argument("--output", help="Web 검증이 추가된 결과 JSON 저장 경로")
    parser.add_argument("--no-cache", action="store_true", help="SQLite 캐시를 사용하지 않고 검색")
    parser.add_argument("--print-json", action="store_true", help="결과를 stdout 에도 출력")
    parser.add_argument("--verbose", action="store_true", help="Rule Engine 단계별 로그 출력")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    agent = WebSearchAgent()

    if args.ocr_json:
        if not args.profile:
            raise SystemExit("--ocr-json 을 사용할 때는 --profile 도 함께 필요합니다.")
        ocr_path = Path(args.ocr_json)
        profile_path = Path(args.profile)
        with ocr_path.open(encoding="utf-8") as f:
            ocr_result = json.load(f)
        with profile_path.open(encoding="utf-8") as f:
            profile = json.load(f)
        result = agent.analyze_and_verify_all(
            ocr_result,
            profile,
            use_cache=not args.no_cache,
            verbose=args.verbose,
        )
        default_output = ocr_path.with_name(f"{ocr_path.stem}_web_verified.json")
    elif args.judged_json:
        judged_path = Path(args.judged_json)
        with judged_path.open(encoding="utf-8") as f:
            judged_result = json.load(f)
        result = agent.verify_all(judged_result, use_cache=not args.no_cache)
        default_output = judged_path.with_name(f"{judged_path.stem}_web_verified.json")
    else:
        raise SystemExit("--judged-json 또는 --ocr-json + --profile 중 하나가 필요합니다.")

    output_path = Path(args.output) if args.output else default_output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Web Search Agent 완료: {len(result.get('menu_analyses', []))}개 메뉴 검증")
    print(f"결과 저장: {output_path}")
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
