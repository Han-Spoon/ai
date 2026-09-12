"""
한스푼 AI - FastAPI 얇은 래퍼

백엔드(Spring) AiClient 가 호출하는 내부 서비스용 HTTP 어댑터.
기존 ai_ocr / ai_ruleengine 로직을 그대로 재사용한다 (비즈니스 로직 중복 없음).

엔드포인트:
    POST /v1/ocr        : S3 IAM 조회(우선) 또는 image_url 다운로드 → OCR 파이프라인 → 최종 dict
    POST /v1/ruleengine : ocr_result + profile → 위험도 판정 dict

실행:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

import importlib.util
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Body, FastAPI, HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

# ── 기존 패키지 모듈을 재사용하기 위한 경로 설정 ──────────────────────────────
# ai_ocr / ai_ruleengine 내부 모듈은 flat import (`from parser import ...`)를
# 사용하므로 각 디렉터리를 sys.path 에 추가한다.
BASE_DIR = Path(__file__).resolve().parent
OCR_DIR = BASE_DIR / "ai_ocr"
RULE_DIR = BASE_DIR / "ai_ruleengine"
# ai_ocr/ai_ruleengine 은 flat import 라 각 디렉터리를, ai_result 는 절대 패키지
# import(`from ai_result....`)라 BASE_DIR 를 sys.path 에 둔다.
for _p in (BASE_DIR, OCR_DIR, RULE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ai_ocr/main.py, ai_ruleengine/main.py, ai_result/main.py 모듈명이 모두 `main`
# 이라 충돌한다. OCR/Result 진입 함수는 파일 경로로 고유 이름 로드해 충돌을 피한다.
_ocr_spec = importlib.util.spec_from_file_location("ai_ocr_main", OCR_DIR / "main.py")
_ocr_main = importlib.util.module_from_spec(_ocr_spec)
_ocr_spec.loader.exec_module(_ocr_main)
analyze_menu_image = _ocr_main.analyze_menu_image

_result_spec = importlib.util.spec_from_file_location(
    "ai_result_main", BASE_DIR / "ai_result" / "main.py"
)
_result_main = importlib.util.module_from_spec(_result_spec)
_result_spec.loader.exec_module(_result_main)
build_final_results_from_judged = _result_main.build_final_results_from_judged

# 룰엔진 진입 함수 (engine 모듈명은 고유라 충돌 없음)
from engine import analyze_all  # noqa: E402
from ocr_client import (  # noqa: E402
    OCRConfigError,
    OCRServiceError,
    validate_clova_config,
)

app = FastAPI(title="Hanspoon AI", version="1.0.0")
logger = logging.getLogger(__name__)
_OCR_CONCURRENCY = max(1, int(os.getenv("OCR_MAX_CONCURRENT_SCANS", "2")))
_OCR_SEMAPHORE = threading.BoundedSemaphore(_OCR_CONCURRENCY)


# ── 요청 모델 ─────────────────────────────────────────────────────────────────
class OcrRequest(BaseModel):
    source: str | None = None
    storage_key: str | None = None
    image_url: str | None = None
    version_id: str | None = None
    expected_etag: str | None = None


class RuleEngineRequest(BaseModel):
    profile: dict
    ocr_result: dict


# ── 헬스체크 ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    try:
        validate_clova_config()
    except OCRConfigError as err:
        logger.error("OCR runtime configuration is invalid: %s", err)
        raise HTTPException(
            status_code=503,
            detail="OCR runtime configuration is invalid.",
        ) from err
    return {"status": "ok"}


# ── 1) OCR ────────────────────────────────────────────────────────────────────
@app.post("/v1/ocr")
def run_ocr(req: OcrRequest):
    """S3 객체 키(우선) 또는 image_url을 스트리밍해 OCR을 실행한다."""
    queue_started_at = time.monotonic()
    request_deadline = queue_started_at + float(
        os.getenv("OCR_REQUEST_BUDGET_SECONDS", "16")
    )
    acquired = _OCR_SEMAPHORE.acquire(
        timeout=float(os.getenv("OCR_QUEUE_WAIT_SECONDS", "1"))
    )
    if not acquired:
        raise HTTPException(
            status_code=503,
            detail="OCR 처리량이 많습니다. 잠시 후 다시 시도해주세요.",
            headers={"Retry-After": "2"},
        )

    queue_wait_ms = int((time.monotonic() - queue_started_at) * 1000)
    tmp_path: str | None = None
    try:
        downloaded = _acquire_image(req)
        tmp_path = downloaded.path
        remaining_budget = request_deadline - time.monotonic()
        if remaining_budget <= 1:
            raise HTTPException(
                status_code=503,
                detail="이미지를 가져오는 동안 OCR 처리 시간 예산이 종료되었습니다.",
            )

        # 요청 전체 예산(큐 대기·다운로드 포함)과 OCR 파이프라인 자체 예산 중
        # 더 작은 값을 사용해 Spring의 18초 read timeout 안에 응답할 여유를 둔다.
        pipeline_budget = min(
            remaining_budget,
            float(os.getenv("OCR_TOTAL_BUDGET_SECONDS", "14")),
        )

        # 기존 OCR 파이프라인 실행. source/storage_key/image_url 그대로 전달.
        result = analyze_menu_image(
            tmp_path,
            source=req.source or "upload",
            storage_key=req.storage_key,
            image_url=req.image_url,
            mime_type=downloaded.mime_type,
            file_size=downloaded.file_size,
            # 사용자 대기 경로에서 외부 GPT 호출을 제거한다.
            enable_gpt_post_process=_env_bool("OCR_ENABLE_GPT_POST_PROCESS", False),
            enable_gpt_judgment=_env_bool("OCR_ENABLE_GPT_JUDGMENT", False),
            total_budget_seconds=pipeline_budget,
        )
        result["final"]["scan_quality"]["image_fetch_source"] = downloaded.source
        result["final"]["scan_quality"]["queue_wait_ms"] = queue_wait_ms
        # build_final_result 가 만든 dict 를 그대로 반환
        return result["final"]
    except HTTPException:
        raise
    except OCRConfigError as err:
        logger.error("OCR request rejected due to invalid runtime configuration: %s", err)
        raise HTTPException(
            status_code=503,
            detail="OCR service configuration is invalid.",
        ) from err
    except OCRServiceError as err:
        logger.warning(
            "OCR upstream request failed (status=%d, retryable=%s)",
            err.status_code,
            err.retryable,
        )
        raise HTTPException(status_code=err.status_code, detail=str(err)) from err
    except Exception as err:  # OCR/GPT 등 파이프라인 오류 → 500
        logger.exception("Unexpected OCR processing failure")
        raise HTTPException(
            status_code=500,
            detail="OCR 처리 중 예상하지 못한 오류가 발생했습니다.",
        ) from err
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        _OCR_SEMAPHORE.release()


# ── 2) Rule Engine ────────────────────────────────────────────────────────────
@app.post("/v1/ruleengine")
def run_ruleengine(req: RuleEngineRequest):
    """analyze_all(ocr_result, profile) 결과 dict 를 그대로 반환한다."""
    try:
        return analyze_all(req.ocr_result, req.profile)
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"룰엔진 처리 실패: {err}") from err


# ── 3) Result ─────────────────────────────────────────────────────────────────
@app.post("/v1/result")
def run_result(judged_result: dict = Body(...)):
    """/v1/ruleengine 응답(judged_result)을 받아 menu_analyses 를 FinalOutput 으로 교체한다.

    GPT 호출은 ai_result 내부(unknown_menu/unknown_remain 케이스)에서만 일어난다.
    """
    try:
        return build_final_results_from_judged(judged_result)
    except Exception as err:
        logger.exception("Unexpected result generation failure")
        raise HTTPException(
            status_code=500,
            detail="결과 생성 중 예상하지 못한 오류가 발생했습니다.",
        ) from err


@dataclass(frozen=True)
class DownloadedImage:
    path: str
    mime_type: str
    file_size: int
    source: str


_STORAGE_KEY_PATTERN = re.compile(
    r"^scans/[0-9a-fA-F-]{36}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:jpg|jpeg|png|webp)$"
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() == "true"


def _acquire_image(req: OcrRequest) -> DownloadedImage:
    if _env_bool("OCR_S3_FETCH_ENABLED", False) and req.storage_key:
        return _download_s3_image(
            req.storage_key,
            version_id=req.version_id,
            expected_etag=req.expected_etag,
        )
    if req.image_url:
        return _download_url_image(req.image_url)
    raise HTTPException(
        status_code=400,
        detail="storage_key(S3 IAM 사용 시) 또는 image_url이 필요합니다.",
    )


def _normalize_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None


def _download_url_image(image_url: str) -> DownloadedImage:
    parsed = urlparse(image_url)
    allow_http = _env_bool("OCR_ALLOW_HTTP", False)
    if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http"):
        raise HTTPException(
            status_code=400, detail="이미지는 HTTPS URL만 사용할 수 있습니다."
        )
    if not parsed.hostname:
        raise HTTPException(
            status_code=400, detail="이미지 URL 호스트가 올바르지 않습니다."
        )

    allowed_hosts = {
        host.strip().lower()
        for host in os.getenv("OCR_ALLOWED_IMAGE_HOSTS", "").split(",")
        if host.strip()
    }
    if _env_bool("OCR_REQUIRE_IMAGE_HOST_ALLOWLIST", False) and not allowed_hosts:
        raise HTTPException(
            status_code=500,
            detail="OCR_ALLOWED_IMAGE_HOSTS 운영 설정이 필요합니다.",
        )
    if allowed_hosts and parsed.hostname.lower() not in allowed_hosts:
        raise HTTPException(
            status_code=400, detail="허용되지 않은 이미지 저장소입니다."
        )

    max_bytes = int(os.getenv("OCR_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
    download_timeout = float(os.getenv("OCR_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "5"))
    tmp_path = _new_temp_path()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(download_timeout, connect=min(2.0, download_timeout)),
            follow_redirects=False,
        ) as client:
            with client.stream("GET", image_url) as response:
                response.raise_for_status()
                mime_type = _normalize_content_type(
                    response.headers.get("content-type")
                )
                if mime_type and not mime_type.startswith("image/"):
                    raise HTTPException(
                        status_code=415,
                        detail=f"이미지 MIME 타입이 아닙니다: {mime_type}",
                    )

                declared_size = int(response.headers.get("content-length") or 0)
                if declared_size > max_bytes:
                    raise HTTPException(
                        status_code=413, detail="이미지 파일이 너무 큽니다."
                    )

                total = _write_chunks(tmp_path, response.iter_bytes(), max_bytes)
    except HTTPException:
        _safe_unlink(tmp_path)
        raise
    except httpx.HTTPError as err:
        _safe_unlink(tmp_path)
        raise HTTPException(
            status_code=502, detail=f"이미지 다운로드 실패: {err}"
        ) from err

    return _validate_and_normalize_image(tmp_path, total, source="presigned_url")


def _download_s3_image(
    storage_key: str,
    *,
    version_id: str | None,
    expected_etag: str | None,
) -> DownloadedImage:
    if not _STORAGE_KEY_PATTERN.fullmatch(storage_key):
        raise HTTPException(
            status_code=400, detail="S3 저장 키 형식이 올바르지 않습니다."
        )

    bucket = os.getenv("OCR_S3_BUCKET") or os.getenv("S3_BUCKET")
    if not bucket:
        raise HTTPException(status_code=500, detail="OCR_S3_BUCKET 설정이 필요합니다.")

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise HTTPException(
            status_code=500, detail="boto3 패키지가 필요합니다."
        ) from error

    request = {"Bucket": bucket, "Key": storage_key}
    if version_id:
        request["VersionId"] = version_id

    max_bytes = int(os.getenv("OCR_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
    tmp_path = _new_temp_path()
    body = None
    try:
        download_timeout = float(os.getenv("OCR_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "5"))
        s3 = boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION", "ap-northeast-2"),
            config=Config(
                connect_timeout=min(2.0, download_timeout),
                read_timeout=download_timeout,
                retries={"total_max_attempts": 2, "mode": "standard"},
            ),
        )
        response = s3.get_object(**request)
        declared_size = int(response.get("ContentLength") or 0)
        if declared_size > max_bytes:
            raise HTTPException(status_code=413, detail="이미지 파일이 너무 큽니다.")

        actual_etag = str(response.get("ETag") or "")
        # 백엔드가 HeadObject로 검증한 객체와 정확히 같은 바이트만 OCR한다.
        # ETag가 누락돼도 비교를 생략하지 않고 fail-closed 한다.
        if expected_etag and expected_etag != actual_etag:
            raise HTTPException(
                status_code=409, detail="검증한 S3 객체와 다운로드 버전이 다릅니다."
            )

        body = response["Body"]
        total = _write_chunks(
            tmp_path,
            iter(lambda: body.read(64 * 1024), b""),
            max_bytes,
        )
    except HTTPException:
        _safe_unlink(tmp_path)
        raise
    except Exception as error:
        _safe_unlink(tmp_path)
        raise HTTPException(
            status_code=502, detail="S3 이미지 다운로드에 실패했습니다."
        ) from error
    finally:
        if body is not None:
            body.close()

    return _validate_and_normalize_image(tmp_path, total, source="s3_iam")


def _new_temp_path() -> str:
    with tempfile.NamedTemporaryFile(
        prefix="hanspoon_download_", suffix=".img", delete=False
    ) as tmp:
        return tmp.name


def _write_chunks(path: str, chunks, max_bytes: int) -> int:
    total = 0
    with open(path, "wb") as output:
        for chunk in chunks:
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=413, detail="이미지 파일이 너무 큽니다."
                )
            output.write(chunk)
    return total


def _validate_and_normalize_image(
    path: str, file_size: int, *, source: str
) -> DownloadedImage:
    try:
        with Image.open(path) as image:
            width, height = image.size
            max_pixels = int(os.getenv("OCR_MAX_IMAGE_PIXELS", "40000000"))
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise HTTPException(
                    status_code=413, detail="이미지 해상도가 허용 범위를 벗어났습니다."
                )
            detected_format = str(image.format or "").upper()
            image.verify()
    except HTTPException:
        _safe_unlink(path)
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as err:
        _safe_unlink(path)
        raise HTTPException(
            status_code=415, detail="유효한 이미지 파일이 아닙니다."
        ) from err

    if detected_format not in {"JPEG", "PNG", "WEBP"}:
        _safe_unlink(path)
        raise HTTPException(
            status_code=415,
            detail=f"지원하지 않는 이미지 형식입니다: {detected_format}",
        )

    suffix = ".jpg" if detected_format in {"JPEG", "WEBP"} else ".png"
    normalized_path = str(Path(path).with_suffix(suffix))
    try:
        with Image.open(path) as image:
            exif_orientation = image.getexif().get(274, 1)

        # 휴대폰 사진의 EXIF 방향값을 실제 픽셀 방향으로 반영한다.
        # OpenCV 기반 전처리는 EXIF를 항상 존중하지 않으므로 OCR 전에 정규화해야 한다.
        if detected_format == "WEBP" or exif_orientation != 1:
            with Image.open(path) as image:
                normalized = ImageOps.exif_transpose(image)
                if suffix == ".jpg":
                    normalized.convert("RGB").save(
                        normalized_path, format="JPEG", quality=95
                    )
                else:
                    normalized.save(normalized_path, format="PNG")
            _safe_unlink(path)
            file_size = os.path.getsize(normalized_path)
        else:
            shutil.move(path, normalized_path)
    except Exception:
        _safe_unlink(path)
        _safe_unlink(normalized_path)
        raise

    return DownloadedImage(
        path=normalized_path,
        mime_type="image/jpeg" if suffix == ".jpg" else "image/png",
        file_size=file_size,
        source=source,
    )


def _safe_unlink(path: str | None) -> None:
    if path and os.path.exists(path):
        os.remove(path)
