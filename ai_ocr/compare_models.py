"""CLOVA 원본 이미지와 전처리 이미지 결과를 비교하는 진단 CLI."""

import argparse
import json
from pathlib import Path

from clova_layout import parse_attempt_score
from main import prepare_ocr_image
from ocr_client import ClovaOCRClient, OCRConfigError
from parser import parse_menu_candidates


def save_json(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as json_file:
        json.dump(data, json_file, ensure_ascii=False, indent=2)


def compare_models(image_path: str):
    image = Path(image_path)
    if not image.exists() or not image.is_file():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")

    client = ClovaOCRClient()
    processed_path = None
    try:
        processed_path = prepare_ocr_image(image_path, True)
        variants = {"original": image, "preprocessed": Path(processed_path)}
        scores = {}

        for variant, path in variants.items():
            tokens = client.analyze_image(str(path))
            menus = parse_menu_candidates(tokens)
            score = parse_attempt_score(tokens, menus)
            scores[variant] = score
            save_json(
                {"score": score, "tokens": tokens, "menus": menus},
                Path("outputs/preprocess_compare") / image.stem / f"{variant}.json",
            )
            print(f"{variant}: 메뉴 {len(menus)}개, 구조 점수 {score}")

        winner = max(scores, key=scores.get)
        print(f"선택 결과: {winner}")
    finally:
        client.close()
        if processed_path and Path(processed_path).exists():
            Path(processed_path).unlink()


def parse_args():
    parser = argparse.ArgumentParser(description="CLOVA OCR 원본/전처리 결과 비교")
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
    except Exception as error:
        print(f"[실행 오류] {error}")


if __name__ == "__main__":
    main()
