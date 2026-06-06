import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from api_response_builder import build_api_response
from parser import parse_menu_candidates
from result_builder import build_final_result


DEFAULT_MODEL_ID = "prebuilt-layout"


def analyze_menu_image(
    image_path: str,
    model_id: str = DEFAULT_MODEL_ID,
    fallback_read: bool = True,
    use_preprocess: bool = True,
    perspective: bool = True,
    deskew: bool = True,
    max_deskew_angle: float = 25.0,
    source: str = "upload",
    storage_key: str | None = None,
    image_url: str | None = None,
    mime_type: str | None = None,
    file_size: int | None = None,
    enable_gpt_post_process: bool = True,
    enable_gpt_judgment: bool = True,
):
    image = Path(image_path)
    if not image.exists() or not image.is_file():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")

    ocr_image_path = prepare_ocr_image(
        image_path,
        use_preprocess,
        perspective=perspective,
        deskew=deskew,
        max_deskew_angle=max_deskew_angle,
    )

    from ocr_client import AzureOCRClient

    client = AzureOCRClient()
    raw_lines, used_model_id = analyze_with_optional_fallback(
        client=client,
        image_path=str(ocr_image_path),
        model_id=model_id,
        fallback_read=fallback_read,
    )
    menus = parse_menu_candidates(raw_lines)

    return {
        "modelId": used_model_id,
        "rawLines": raw_lines,
        "final": build_final_result(
            image_path,
            menus,
            source=source,
            storage_key=storage_key,
            image_url=image_url,
            mime_type=mime_type,
            file_size=file_size,
            raw_lines=raw_lines,
            enable_gpt_post_process=enable_gpt_post_process,
            enable_gpt_judgment=enable_gpt_judgment,
        ),
    }


def analyze_menu_image_by_url(
    image_url: str,
    model_id: str = DEFAULT_MODEL_ID,
    fallback_read: bool = True,
    source: str = "upload",
    storage_key: str | None = None,
    mime_type: str | None = None,
    file_size: int | None = None,
    enable_gpt_post_process: bool = True,
    enable_gpt_judgment: bool = True,
):
    from ocr_client import AzureOCRClient

    client = AzureOCRClient()
    raw_lines, used_model_id = analyze_url_with_optional_fallback(
        client=client,
        image_url=image_url,
        model_id=model_id,
        fallback_read=fallback_read,
    )
    menus = parse_menu_candidates(raw_lines)
    image_title = infer_image_title(storage_key, image_url)

    return {
        "modelId": used_model_id,
        "rawLines": raw_lines,
        "final": build_final_result(
            image_title,
            menus,
            source=source,
            storage_key=storage_key,
            image_url=image_url,
            mime_type=mime_type,
            file_size=file_size,
            image_title=image_title,
            raw_lines=raw_lines,
            enable_gpt_post_process=enable_gpt_post_process,
            enable_gpt_judgment=enable_gpt_judgment,
        ),
    }


def prepare_ocr_image(
    image_path: str,
    use_preprocess: bool,
    perspective: bool = True,
    deskew: bool = True,
    max_deskew_angle: float = 25.0,
):
    if not use_preprocess:
        return Path(image_path)

    source = Path(image_path)
    output_path = Path("images/preprocessed") / f"{source.stem}_preprocessed{source.suffix}"
    from preprocess_image import preprocess_image

    processed_path = preprocess_image(
        input_path=image_path,
        output_path=str(output_path),
        crop=None,
        scale=2.0,
        grayscale=True,
        contrast=1.4,
        sharpness=1.6,
        perspective=perspective,
        deskew=deskew,
        max_deskew_angle=max_deskew_angle,
    )
    print(f"전처리 이미지 사용: {processed_path}")
    return processed_path


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


def analyze_url_with_optional_fallback(client, image_url: str, model_id: str, fallback_read: bool):
    try:
        raw_lines = client.analyze_image_url(image_url, model_id=model_id)
        if raw_lines:
            return raw_lines, model_id

        if fallback_read and model_id == "prebuilt-layout":
            print("[안내] prebuilt-layout 결과 line이 0개라서 prebuilt-read로 한 번만 재시도합니다.")
            return client.analyze_image_url(image_url, model_id="prebuilt-read"), "prebuilt-read"

        return raw_lines, model_id
    except Exception as error:
        if fallback_read and model_id == "prebuilt-layout":
            print(f"[안내] prebuilt-layout 호출 실패: {error}")
            print("[안내] prebuilt-read로 한 번만 재시도합니다.")
            return client.analyze_image_url(image_url, model_id="prebuilt-read"), "prebuilt-read"

        raise


def save_json(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as json_file:
        json.dump(data, json_file, ensure_ascii=False, indent=2)


def build_output_paths(image_ref: str, model_id: str):
    image_stem = Path(image_ref).stem
    safe_model_id = model_id.replace("/", "_")
    raw_path = Path("outputs/raw") / f"{image_stem}_{safe_model_id}_raw.json"
    final_path = Path("outputs/final") / f"{image_stem}_result.json"
    api_path = Path("outputs/api") / f"{image_stem}_api_result.json"
    return raw_path, final_path, api_path


def infer_image_title(storage_key: str | None, image_url: str):
    if storage_key:
        return Path(storage_key).name

    parsed = urlparse(image_url)
    name = Path(parsed.path).name
    return name or "blob_image"


def parse_args():
    parser = argparse.ArgumentParser(description="Azure OCR 메뉴판 구조화 실행")
    parser.add_argument("--image", help="분석할 메뉴판 이미지 경로")
    parser.add_argument(
        "--ocr-image-url",
        help="Azure OCR이 읽을 수 있는 Blob read SAS URL. 지정하면 로컬 이미지 대신 URL로 분석",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL_ID, help="Azure Document Intelligence 모델 ID")
    parser.add_argument(
        "--no-fallback-read",
        action="store_true",
        help="prebuilt-layout 실패 시 prebuilt-read 재시도를 하지 않음",
    )
    parser.add_argument(
        "--no-preprocess",
        action="store_true",
        help="로컬 이미지 전처리를 하지 않고 원본 이미지를 Azure OCR에 전달",
    )
    parser.add_argument(
        "--no-perspective",
        action="store_true",
        help="전처리 중 자동 원근 보정을 하지 않음",
    )
    parser.add_argument(
        "--no-deskew",
        action="store_true",
        help="전처리 중 자동 기울기 보정을 하지 않음",
    )
    parser.add_argument(
        "--max-deskew-angle",
        type=float,
        default=25.0,
        help="자동 회전 보정 최대 각도",
    )
    parser.add_argument(
        "--source",
        choices=("camera", "upload"),
        default="upload",
        help="이미지 입력 출처. 백엔드 menu_images.source 저장값",
    )
    parser.add_argument(
        "--storage-key",
        help="백엔드 또는 외부 저장소에 저장된 이미지 키. menu_images.storage_key에 들어감",
    )
    parser.add_argument(
        "--image-url",
        help="백엔드 또는 외부 저장소 이미지 접근 URL. menu_images.image_url에 들어감",
    )
    parser.add_argument(
        "--mime-type",
        help="백엔드가 전달한 이미지 MIME 타입. menu_images.mime_type에 들어감",
    )
    parser.add_argument(
        "--file-size",
        type=int,
        help="백엔드가 전달한 이미지 파일 크기. menu_images.file_size에 들어감",
    )
    parser.add_argument(
        "--language-code",
        choices=("ko", "en", "ar"),
        default="ko",
        help="API 응답용 사용자 언어 코드",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="최종 JSON을 stdout에도 출력함. 백엔드 연동 테스트용",
    )
    parser.add_argument(
        "--no-gpt-post-process",
        action="store_true",
        help="GPT-4o-mini를 사용한 메뉴명/설명 자동 수정을 하지 않음",
    )
    parser.add_argument(
        "--no-gpt-judgment",
        action="store_true",
        help="GPT-4o-mini를 사용한 OCR 품질 판단을 하지 않음",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        if args.ocr_image_url:
            result = analyze_menu_image_by_url(
                args.ocr_image_url,
                args.model,
                fallback_read=not args.no_fallback_read,
                source=args.source,
                storage_key=args.storage_key,
                mime_type=args.mime_type,
                file_size=args.file_size,
                enable_gpt_post_process=not args.no_gpt_post_process,
                enable_gpt_judgment=not args.no_gpt_judgment,
            )
            output_ref = infer_image_title(args.storage_key, args.ocr_image_url)
        else:
            if not args.image:
                raise ValueError("--image 또는 --ocr-image-url 중 하나는 필요합니다.")

            result = analyze_menu_image(
                args.image,
                args.model,
                fallback_read=not args.no_fallback_read,
                use_preprocess=not args.no_preprocess,
                perspective=not args.no_perspective,
                deskew=not args.no_deskew,
                max_deskew_angle=args.max_deskew_angle,
                source=args.source,
                storage_key=args.storage_key,
                image_url=args.image_url,
                mime_type=args.mime_type,
                file_size=args.file_size,
                enable_gpt_post_process=not args.no_gpt_post_process,
                enable_gpt_judgment=not args.no_gpt_judgment,
            )
            output_ref = args.image

        raw_path, final_path, api_path = build_output_paths(output_ref, result["modelId"])
        api_result = build_api_response(result["final"], language_code=args.language_code)

        save_json(result["rawLines"], raw_path)
        save_json(result["final"], final_path)
        save_json(api_result, api_path)

        print(f"OCR raw line 저장 완료: {raw_path}")
        print(f"최종 JSON 저장 완료: {final_path}")
        print(f"API 응답용 JSON 저장 완료: {api_path}")
        print(f"메뉴 후보 {result['final']['scan_session']['menu_count']}개 추출")
        if args.print_json:
            print(json.dumps(result["final"], ensure_ascii=False, indent=2))
    except FileNotFoundError as error:
        print(f"[파일 오류] {error}")
    except RuntimeError as error:
        print(f"[환경 설정 오류] {error}")
        print(".env.example을 참고해서 .env 파일을 작성해주세요.")
    except Exception as error:
        print(f"[실행 오류] {error}")


if __name__ == "__main__":
    main()
