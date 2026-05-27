import argparse
import json
from pathlib import Path

from ocr_client import SUPPORTED_MODEL_IDS, AzureOCRClient, OCRConfigError


def save_json(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as json_file:
        json.dump(data, json_file, ensure_ascii=False, indent=2)


def compare_models(image_path: str):
    image = Path(image_path)
    if not image.exists() or not image.is_file():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")

    client = AzureOCRClient()
    image_stem = Path(image_path).stem

    for model_id in SUPPORTED_MODEL_IDS:
        print(f"\n모델 실행: {model_id}")
        output_path = Path("outputs/model_compare") / image_stem / f"{model_id}.json"

        try:
            lines = client.analyze_image(image_path, model_id=model_id)
            save_json(lines, output_path)

            print(f"저장 완료: {output_path}")
            print(f"line 개수: {len(lines)}")
            print("추출 텍스트:")
            for index, line in enumerate(lines, start=1):
                print(f"  {index}. {line['text']}")
        except Exception as error:
            error_path = output_path.with_suffix(".error.json")
            save_json({"modelId": model_id, "error": str(error)}, error_path)
            print(f"[모델 오류] {model_id}: {error}")
            print(f"오류 저장 완료: {error_path}")


def parse_args():
    parser = argparse.ArgumentParser(description="Azure OCR 모델별 결과 비교")
    parser.add_argument("--image", required=True, help="비교할 메뉴판 이미지 경로")
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        compare_models(args.image)
    except FileNotFoundError as error:
        print(f"[파일 오류] {error}")
    except OCRConfigError as error:
        print(f"[환경 설정 오류] {error}")
        print(".env.example을 참고해서 .env 파일을 작성해주세요.")
    except Exception as error:
        print(f"[실행 오류] {error}")


if __name__ == "__main__":
    main()
