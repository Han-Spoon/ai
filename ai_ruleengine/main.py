"""
한스푼 Rule Engine - CLI 진입점

사용법:
    python3 ai_ruleengine/main.py \\
        --ocr-json outputs/final/menu_001_result.json \\
        --profile profile.json \\
        --output outputs/final/menu_001_judged.json

ai_ocr/main.py 와 동일한 CLI 스타일 준수.
"""

import argparse
import json
import sys
from pathlib import Path

# ai_ruleengine 디렉터리를 sys.path 에 추가 (직접 실행 시)
sys.path.insert(0, str(Path(__file__).parent))

from engine import analyze_all


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="한스푼 Rule Engine — 메뉴 위험도 판정")
    parser.add_argument(
        "--ocr-json",
        required=True,
        help="ai_ocr 이 생성한 _result.json 파일 경로",
    )
    parser.add_argument(
        "--profile",
        required=True,
        help=(
            "사용자 프로필 JSON 파일 경로. "
            "키: religion_type, is_vegetarian, vegetarian_type, no_alcohol, allergies, no_spicy"
        ),
    )
    parser.add_argument(
        "--output",
        help="결과 JSON 저장 경로. 미지정 시 --ocr-json 동일 디렉터리에 _judged.json 으로 저장",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="최종 JSON 을 stdout 에도 출력. 백엔드 연동 테스트용",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="각 메뉴별 단계별 처리 내용을 콘솔에 출력",
    )
    return parser.parse_args()


def build_output_path(ocr_json_path: Path) -> Path:
    stem = ocr_json_path.stem
    if stem.endswith("_result"):
        stem = stem[: -len("_result")]
    return ocr_json_path.parent / f"{stem}_judged.json"


def main() -> None:
    args = parse_args()

    ocr_path = Path(args.ocr_json)
    profile_path = Path(args.profile)

    if not ocr_path.exists():
        print(f"[파일 오류] OCR JSON 파일을 찾을 수 없습니다: {ocr_path}")
        sys.exit(1)
    if not profile_path.exists():
        print(f"[파일 오류] 프로필 파일을 찾을 수 없습니다: {profile_path}")
        sys.exit(1)

    try:
        ocr_result = load_json(ocr_path)
        profile = load_json(profile_path)
    except json.JSONDecodeError as err:
        print(f"[JSON 파싱 오류] {err}")
        sys.exit(1)

    judged = analyze_all(ocr_result, profile, verbose=args.verbose)

    output_path = Path(args.output) if args.output else build_output_path(ocr_path)
    save_json(judged, output_path)

    menu_count = len(judged.get("menu_analyses", []))
    risky_count = judged.get("scan_session", {}).get("risky_menu_count", 0)
    print(f"Rule Engine 완료: 메뉴 {menu_count}개 중 위험/주의 {risky_count}개")
    print(f"결과 저장: {output_path}")

    if args.print_json:
        print(json.dumps(judged, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
