"""NAVER CLOVA General OCR V2 클라이언트."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

import httpx
from clova_layout import extract_clova_fields
from dotenv import load_dotenv

DEFAULT_MODEL_ID = "clova-general-v2"
SUPPORTED_IMAGE_FORMATS = {"jpg", "jpeg", "png", "pdf", "tif", "tiff"}
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class OCRConfigError(RuntimeError):
    pass


class OCRServiceError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int = 502, retryable: bool = False
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class _RequestRateLimiter:
    """단일 AI 인스턴스에서 CLOVA 호출 시작 간격을 제한한다."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = max(min_interval_seconds, 0.0)
        self._lock = threading.Lock()
        self._last_started_at = 0.0

    def wait(self, deadline_monotonic: float | None = None):
        with self._lock:
            now = time.monotonic()
            delay = self.min_interval_seconds - (now - self._last_started_at)
            if delay > 0:
                if deadline_monotonic is not None and now + delay >= deadline_monotonic:
                    raise OCRServiceError(
                        "OCR 처리 시간 예산이 부족합니다.",
                        status_code=503,
                        retryable=False,
                    )
                time.sleep(delay)
            self._last_started_at = time.monotonic()


_RATE_LIMITER_REGISTRY: dict[float, _RequestRateLimiter] = {}
_RATE_LIMITER_REGISTRY_LOCK = threading.Lock()


def _shared_rate_limiter(min_interval_seconds: float) -> _RequestRateLimiter:
    with _RATE_LIMITER_REGISTRY_LOCK:
        return _RATE_LIMITER_REGISTRY.setdefault(
            min_interval_seconds,
            _RequestRateLimiter(min_interval_seconds),
        )


class ClovaOCRClient:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        deadline_monotonic: float | None = None,
        max_calls_per_scan: int | None = None,
    ):
        load_dotenv()

        self.endpoint = os.getenv("CLOVA_OCR_URL")
        self.secret = os.getenv("CLOVA_OCR_SECRET")
        if not self.endpoint or not self.secret:
            raise OCRConfigError("CLOVA_OCR_URL과 CLOVA_OCR_SECRET을 설정해주세요.")

        self.timeout_seconds = float(os.getenv("CLOVA_OCR_TIMEOUT_SECONDS", "10"))
        self.max_attempts = max(1, int(os.getenv("CLOVA_OCR_MAX_ATTEMPTS", "2")))
        self.max_calls_per_scan = max(
            1,
            max_calls_per_scan
            if max_calls_per_scan is not None
            else int(os.getenv("CLOVA_OCR_MAX_CALLS_PER_SCAN", "2")),
        )
        self.deadline_monotonic = deadline_monotonic
        self.calls_started = 0
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(
                self.timeout_seconds, connect=min(3.0, self.timeout_seconds)
            ),
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._rate_limiter = _shared_rate_limiter(
            float(os.getenv("CLOVA_OCR_MIN_INTERVAL_SECONDS", "1.0"))
        )

    def analyze_image(
        self, image_path: str, model_id: str = DEFAULT_MODEL_ID
    ) -> list[dict]:
        image = Path(image_path)
        if not image.exists() or not image.is_file():
            raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")

        max_bytes = int(os.getenv("OCR_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
        if image.stat().st_size > max_bytes:
            raise ValueError("OCR 이미지 파일이 허용 크기를 초과했습니다.")

        image_format = _infer_clova_format(image.suffix)
        payload = self._request_payload(
            image_format=image_format,
            image_name=image.stem or "menu",
            allow_empty_source=True,
        )
        return self._extract_fields(self._post_file(image, payload))

    def analyze_image_url(
        self, image_url: str, model_id: str = DEFAULT_MODEL_ID
    ) -> list[dict]:
        if not image_url:
            raise ValueError("이미지 URL이 비어 있습니다.")

        suffix = Path(httpx.URL(image_url).path).suffix
        payload = self._request_payload(
            image_format=_infer_clova_format(suffix),
            image_name=Path(httpx.URL(image_url).path).stem or "menu",
            url=image_url,
        )
        return self._extract_fields(self._post_json(payload))

    def can_start_call(self, min_remaining_seconds: float = 0.0) -> bool:
        if self.calls_started >= self.max_calls_per_scan:
            return False
        if self.deadline_monotonic is None:
            return True
        return self.remaining_seconds() > min_remaining_seconds

    def remaining_seconds(self) -> float:
        if self.deadline_monotonic is None:
            return float("inf")
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def close(self):
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _extract_fields(response_payload: dict) -> list[dict]:
        try:
            return extract_clova_fields(response_payload)
        except ValueError as error:
            raise OCRServiceError(f"CLOVA OCR 응답 오류: {error}") from error

    def _request_payload(
        self,
        *,
        image_format: str,
        image_name: str,
        data: str | None = None,
        url: str | None = None,
        allow_empty_source: bool = False,
    ) -> dict:
        image: dict[str, str] = {"format": image_format, "name": image_name}
        if data is not None:
            image["data"] = data
        elif url is not None:
            image["url"] = url
        elif not allow_empty_source:
            raise ValueError("CLOVA OCR 요청에는 data 또는 url이 필요합니다.")

        return {
            "version": "V2",
            "requestId": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "lang": "ko",
            "images": [image],
            "enableTableDetection": False,
        }

    def _post_file(self, image: Path, payload: dict) -> dict:
        def send(timeout: httpx.Timeout):
            with image.open("rb") as image_file:
                return self._client.post(
                    self.endpoint,
                    headers={"X-OCR-SECRET": self.secret},
                    data={"message": json.dumps(payload, ensure_ascii=False)},
                    files={
                        "file": (
                            image.name,
                            image_file,
                            _mime_type_for_format(payload["images"][0]["format"]),
                        )
                    },
                    timeout=timeout,
                )

        return self._post_with_retry(send)

    def _post_json(self, payload: dict) -> dict:
        def send(timeout: httpx.Timeout):
            return self._client.post(
                self.endpoint,
                headers={
                    "X-OCR-SECRET": self.secret,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
            )

        return self._post_with_retry(send)

    def _post_with_retry(self, send) -> dict:
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            if not self.can_start_call():
                raise OCRServiceError(
                    "OCR 호출 예산을 모두 사용했습니다.",
                    status_code=503,
                    retryable=False,
                ) from last_error

            self._rate_limiter.wait(self.deadline_monotonic)
            self.calls_started += 1
            try:
                response = send(self._request_timeout())
                if response.status_code in RETRYABLE_STATUS_CODES:
                    raise OCRServiceError(
                        f"CLOVA OCR 일시 오류: HTTP {response.status_code}",
                        status_code=503 if response.status_code == 429 else 502,
                        retryable=True,
                    )
                if response.is_error:
                    raise OCRServiceError(
                        f"CLOVA OCR 요청 거부: HTTP {response.status_code}",
                        status_code=502,
                        retryable=False,
                    )
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
                service_error = OCRServiceError(
                    "CLOVA OCR에 연결하지 못했습니다.", status_code=503, retryable=True
                )
            except OCRServiceError as error:
                last_error = error
                service_error = error
            except ValueError as error:
                raise OCRServiceError(
                    "CLOVA OCR 응답 JSON이 올바르지 않습니다."
                ) from error

            if (
                not service_error.retryable
                or attempt == self.max_attempts
                or not self.can_start_call()
            ):
                raise service_error from last_error
            backoff = min(0.25 * (2 ** (attempt - 1)), 1.0)
            if self.remaining_seconds() <= backoff:
                raise OCRServiceError(
                    "OCR 재시도 전에 처리 시간 예산이 종료됐습니다.",
                    status_code=503,
                    retryable=False,
                ) from last_error
            time.sleep(backoff)

        raise OCRServiceError("CLOVA OCR 호출에 실패했습니다.") from last_error

    def _request_timeout(self) -> httpx.Timeout:
        remaining = self.remaining_seconds()
        timeout_seconds = min(self.timeout_seconds, remaining)
        if timeout_seconds <= 0.1:
            raise OCRServiceError(
                "OCR 처리 시간 예산이 종료되었습니다.",
                status_code=503,
                retryable=False,
            )
        return httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds))


def _infer_clova_format(suffix: str) -> str:
    image_format = suffix.lower().lstrip(".") or "jpg"
    if image_format == "jpeg":
        return "jpg"
    if image_format not in SUPPORTED_IMAGE_FORMATS:
        raise ValueError(f"지원하지 않는 OCR 이미지 형식입니다: {suffix or '(없음)'}")
    return image_format


def _mime_type_for_format(image_format: str) -> str:
    if image_format in {"jpg", "jpeg"}:
        return "image/jpeg"
    if image_format == "png":
        return "image/png"
    if image_format in {"tif", "tiff"}:
        return "image/tiff"
    if image_format == "pdf":
        return "application/pdf"
    return "application/octet-stream"
