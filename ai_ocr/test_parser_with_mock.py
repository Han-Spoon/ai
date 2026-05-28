import argparse
import json
from pathlib import Path

from parser import parse_menu_candidates
from result_builder import build_final_result


def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"mock OCR line 파일을 찾을 수 없습니다: {path}")

    with path.open("r", encoding="utf-8") as json_file:
        return json.load(json_file)


def save_json(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as json_file:
        json.dump(data, json_file, ensure_ascii=False, indent=2)


def run_mock_parser(input_path: str):
    source_path = Path(input_path)
    lines = load_json(source_path)
    menus = parse_menu_candidates(lines)

    result = build_final_result(source_path.name, menus, raw_lines=lines)

    output_path = Path("outputs/final") / f"{source_path.stem}_result.json"
    save_json(result, output_path)

    print(f"mock parser 결과 저장 완료: {output_path}")
    print(f"메뉴 후보 {len(menus)}개 추출")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def parse_args():
    parser = argparse.ArgumentParser(description="mock OCR line JSON으로 parser만 테스트")
    parser.add_argument("--input", required=True, help="mock OCR line JSON 경로")
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        run_mock_parser(args.input)
    except FileNotFoundError as error:
        print(f"[파일 오류] {error}")
    except json.JSONDecodeError as error:
        print(f"[JSON 오류] mock 파일 형식이 올바르지 않습니다: {error}")
    except Exception as error:
        print(f"[실행 오류] {error}")


if __name__ == "__main__":
    main()
