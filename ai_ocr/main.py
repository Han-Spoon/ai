import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from api_response_builder import build_api_response
from parser import parse_menu_candidates
from result_builder import build_final_result


DEFAULT_MODEL_ID = "clova-general-v2"


def analyze_menu_image(
    image_path: str,
    model_id: str = DEFAULT_MODEL_ID,
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
    total_budget_seconds: float | None = None,
):
    image = Path(image_path)
    if not image.exists() or not image.is_file():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")

    started_at = time.monotonic()
    if total_budget_seconds is None:
        total_budget_seconds = float(os.getenv("OCR_TOTAL_BUDGET_SECONDS", "14"))
    retry_min_remaining = float(
        os.getenv("OCR_QUALITY_RETRY_MIN_REMAINING_SECONDS", "8")
    )
    deadline = started_at + max(total_budget_seconds, 1.0)
    preprocessed_path = None
    preprocessing_attempted = False
    retry_skipped_reason = None
    try:
        from clova_layout import parse_attempt_score, should_retry_with_preprocessing
        from image_quality import analyze_image_quality, should_preprocess_before_ocr
        from ocr_client import ClovaOCRClient

        input_quality = analyze_image_quality(image)
        first_input = image
        first_attempt_name = "original"

        # 원본+재시도 2회 대기 대신 보정본을 첫 입력으로 선택.
        if use_preprocess and should_preprocess_before_ocr(input_quality):
            try:
                preprocessed_path = prepare_ocr_image(
                    image_path,
                    True,
                    perspective=perspective,
                    deskew=deskew,
                    max_deskew_angle=max_deskew_angle,
                )
                preprocessing_attempted = True
                first_input = Path(preprocessed_path)
                first_attempt_name = "preprocessed"
            except (OSError, RuntimeError, ValueError) as error:
                print(f"[경고] OCR 사전 전처리 실패, 원본 사용: {error}")

        client = ClovaOCRClient(
            deadline_monotonic=deadline,
            max_calls_per_scan=int(os.getenv("CLOVA_OCR_MAX_CALLS_PER_SCAN", "2")),
        )
        raw_lines = client.analyze_image(str(first_input), model_id=model_id)
        menus = parse_menu_candidates(raw_lines)
        selected_attempt = first_attempt_name

        # 첫 OCR이 구조적으로 불량하고 전체 deadline이 충분할 때만
        # 반대 입력(원본 또는 보정본)으로 단 한 번 품질 재시도한다.
        if use_preprocess and should_retry_with_preprocessing(raw_lines, menus):
            if not client.can_start_call(retry_min_remaining):
                retry_skipped_reason = "insufficient_time_or_call_budget"
            else:
                try:
                    if first_attempt_name == "preprocessed":
                        retry_input = image
                        retry_attempt_name = "original"
                    else:
                        preprocessed_path = prepare_ocr_image(
                            image_path,
                            True,
                            perspective=perspective,
                            deskew=deskew,
                            max_deskew_angle=max_deskew_angle,
                        )
                        preprocessing_attempted = True
                        retry_input = Path(preprocessed_path)
                        retry_attempt_name = "preprocessed"

                    retry_lines = client.analyze_image(
                        str(retry_input), model_id=model_id
                    )
                    retry_menus = parse_menu_candidates(retry_lines)
                    retry_score = parse_attempt_score(retry_lines, retry_menus)
                    first_score = parse_attempt_score(raw_lines, menus)
                    if retry_score > first_score:
                        raw_lines = retry_lines
                        menus = retry_menus
                        selected_attempt = retry_attempt_name
                except (OSError, RuntimeError, ValueError) as error:
                    # 보정/대체 경로의 실패 때문에 이미 얻은 OCR 결과까지 버리지 않는다.
                    retry_skipped_reason = "alternate_attempt_failed"
                    print(f"[경고] OCR 대체 입력 재시도 실패, 첫 결과 사용: {error}")

        final = build_final_result(
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
        )
        processing_time_ms = int((time.monotonic() - started_at) * 1000)
        final["scan_quality"].update(
            {
                "preprocessing_attempted": preprocessing_attempted,
                "preprocessing_applied": selected_attempt == "preprocessed",
                "selected_ocr_attempt": selected_attempt,
                "ocr_attempt_count": client.calls_started,
                "retry_skipped_reason": retry_skipped_reason,
                "ocr_processing_time_ms": processing_time_ms,
                "ocr_budget_ms": int(total_budget_seconds * 1000),
            }
        )

        return {
            "modelId": model_id,
            "rawLines": raw_lines,
            "preprocessingApplied": selected_attempt == "preprocessed",
            "final": final,
        }
    finally:
        if preprocessed_path and Path(preprocessed_path).exists():
            Path(preprocessed_path).unlink()
        if "client" in locals():
            client.close()


def analyze_menu_image_by_url(
    image_url: str,
    model_id: str = DEFAULT_MODEL_ID,
    source: str = "upload",
    storage_key: str | None = None,
    mime_type: str | None = None,
    file_size: int | None = None,
    enable_gpt_post_process: bool = True,
    enable_gpt_judgment: bool = True,
):
    from ocr_client import ClovaOCRClient

    client = ClovaOCRClient()
    try:
        raw_lines = client.analyze_image_url(image_url, model_id=model_id)
        menus = parse_menu_candidates(raw_lines)
        image_title = infer_image_title(storage_key, image_url)

        return {
            "modelId": model_id,
            "rawLines": raw_lines,
            "preprocessingApplied": False,
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
    finally:
        client.close()


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
    with tempfile.NamedTemporaryFile(
        prefix=f"{source.stem}_preprocessed_", suffix=".jpg", delete=False
    ) as temporary:
        output_path = Path(temporary.name)
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
    parser = argparse.ArgumentParser(description="CLOVA General OCR 메뉴판 구조화 실행")
    parser.add_argument("--image", help="분석할 메뉴판 이미지 경로")
    parser.add_argument(
        "--ocr-image-url",
        help="CLOVA OCR이 읽을 수 있는 HTTPS 이미지 URL. 지정하면 로컬 이미지 대신 URL로 분석",
    )
    parser.add_argument(
        "--no-preprocess",
        action="store_true",
        help="원본 결과가 불량해도 전처리 재시도를 하지 않음",
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
