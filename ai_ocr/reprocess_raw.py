import argparse
import json
from pathlib import Path

from parser import parse_menu_candidates
from result_builder import build_final_result


def load_json(path: Path):
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"raw OCR JSON 파일을 찾을 수 없습니다: {path}")

    with path.open("r", encoding="utf-8") as json_file:
        return json.load(json_file)


def save_json(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as json_file:
        json.dump(data, json_file, ensure_ascii=False, indent=2)


def infer_image_name(raw_path: Path):
    name = raw_path.name
    for suffix in ("_prebuilt-layout_raw.json", "_prebuilt-read_raw.json", "_prebuilt-document_raw.json", "_raw.json"):
        if name.endswith(suffix):
            return f"{name.removesuffix(suffix)}.jpg"
    return raw_path.stem + ".jpg"


def infer_image_path(raw_path: Path):
    image_name = infer_image_name(raw_path)
    image_path = Path("images") / image_name
    if image_path.exists():
        return str(image_path)
    return image_name


def infer_model_id(raw_path: Path):
    name = raw_path.name
    if "_prebuilt-layout_raw.json" in name:
        return "prebuilt-layout"
    if "_prebuilt-read_raw.json" in name:
        return "prebuilt-read"
    if "_prebuilt-document_raw.json" in name:
        return "prebuilt-document"
    return "unknown"


def reprocess_raw(input_path: str, output_path: str | None = None):
    raw_path = Path(input_path)
    raw_lines = load_json(raw_path)
    menus = parse_menu_candidates(raw_lines)

    result = build_final_result(infer_image_path(raw_path), menus, raw_lines=raw_lines)

    target_path = Path(output_path) if output_path else Path("outputs/final") / f"{Path(result['scan_session']['title']).stem}_result.json"
    save_json(result, target_path)

    print(f"후처리 결과 저장 완료: {target_path}")
    print(f"메뉴 후보 {len(menus)}개 추출")


def parse_args():
    parser = argparse.ArgumentParser(description="저장된 raw OCR JSON을 Azure 재호출 없이 다시 후처리")
    parser.add_argument("--input", required=True, help="outputs/raw/*_raw.json 경로")
    parser.add_argument("--output", help="저장할 final JSON 경로")
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        reprocess_raw(args.input, args.output)
    except json.JSONDecodeError as error:
        print(f"[JSON 오류] raw 파일 형식이 올바르지 않습니다: {error}")
    except Exception as error:
        print(f"[실행 오류] {error}")


if __name__ == "__main__":
    main()
