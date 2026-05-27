import argparse
import json
from pathlib import Path

from ocr_client import DEFAULT_MODEL_ID, AzureOCRClient, OCRConfigError
from parser import parse_menu_candidates


def analyze_menu_image(image_path: str, model_id: str = DEFAULT_MODEL_ID, fallback_read: bool = True):
    image = Path(image_path)
    if not image.exists() or not image.is_file():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")

    client = AzureOCRClient()
    raw_lines, used_model_id = analyze_with_optional_fallback(
        client=client,
        image_path=image_path,
        model_id=model_id,
        fallback_read=fallback_read,
    )
    menus = parse_menu_candidates(raw_lines)

    return {
        "image": Path(image_path).name,
        "modelId": used_model_id,
        "menus": menus,
        "rawLines": raw_lines,
    }


def analyze_with_optional_fallback(client, image_path: str, model_id: str, fallback_read: bool):
    try:
        raw_lines = client.analyze_image(image_path, model_id=model_id)
        if raw_lines:
            return raw_lines, model_id

        if fallback_read and model_id == "prebuilt-layout":
            print("[안내] prebuilt-layout 결과 line이 0개라서 prebuilt-read로 한 번만 재시도합니다.")
            return client.analyze_image(image_path, model_id="prebuilt-read"), "prebuilt-read"

        return raw_lines, model_id
    except Exception as error:
        if fallback_read and model_id == "prebuilt-layout":
            print(f"[안내] prebuilt-layout 호출 실패: {error}")
            print("[안내] prebuilt-read로 한 번만 재시도합니다.")
            return client.analyze_image(image_path, model_id="prebuilt-read"), "prebuilt-read"

        raise


def save_json(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as json_file:
        json.dump(data, json_file, ensure_ascii=False, indent=2)


def build_output_paths(image_path: str, model_id: str):
    image_stem = Path(image_path).stem
    safe_model_id = model_id.replace("/", "_")
    raw_path = Path("outputs/raw") / f"{image_stem}_{safe_model_id}_raw.json"
    final_path = Path("outputs/final") / f"{image_stem}_result.json"
    return raw_path, final_path


def parse_args():
    parser = argparse.ArgumentParser(description="Azure OCR 메뉴판 구조화 실행")
    parser.add_argument("--image", required=True, help="분석할 메뉴판 이미지 경로")
    parser.add_argument("--model", default=DEFAULT_MODEL_ID, help="Azure Document Intelligence 모델 ID")
    parser.add_argument(
        "--no-fallback-read",
        action="store_true",
        help="prebuilt-layout 실패 시 prebuilt-read 재시도를 하지 않음",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        result = analyze_menu_image(
            args.image,
            args.model,
            fallback_read=not args.no_fallback_read,
        )
        raw_path, final_path = build_output_paths(args.image, result["modelId"])

        save_json(result["rawLines"], raw_path)
        save_json(result, final_path)

        print(f"OCR raw line 저장 완료: {raw_path}")
        print(f"최종 JSON 저장 완료: {final_path}")
        print(f"메뉴 후보 {len(result['menus'])}개 추출")
    except FileNotFoundError as error:
        print(f"[파일 오류] {error}")
    except OCRConfigError as error:
        print(f"[환경 설정 오류] {error}")
        print(".env.example을 참고해서 .env 파일을 작성해주세요.")
    except Exception as error:
        print(f"[실행 오류] {error}")


if __name__ == "__main__":
    main()
