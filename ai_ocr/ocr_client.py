"""NAVER CLOVA General OCR V2 클라이언트."""

from __future__ import annotations

import base64
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

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self.min_interval_seconds - (now - self._last_started_at)
            if delay > 0:
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
    def __init__(self, *, client: httpx.Client | None = None):
        load_dotenv()

        self.endpoint = os.getenv("CLOVA_OCR_URL")
        self.secret = os.getenv("CLOVA_OCR_SECRET")
        if not self.endpoint or not self.secret:
            raise OCRConfigError("CLOVA_OCR_URL과 CLOVA_OCR_SECRET을 설정해주세요.")

        timeout_seconds = float(os.getenv("CLOVA_OCR_TIMEOUT_SECONDS", "60"))
        self.max_attempts = max(1, int(os.getenv("CLOVA_OCR_MAX_ATTEMPTS", "3")))
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
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
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        payload = self._request_payload(
            image_format=image_format,
            image_name=image.stem or "menu",
            data=encoded,
        )
        return self._extract_fields(self._post(payload))

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
        return self._extract_fields(self._post(payload))

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
    ) -> dict:
        image: dict[str, str] = {"format": image_format, "name": image_name}
        if data is not None:
            image["data"] = data
        elif url is not None:
            image["url"] = url
        else:
            raise ValueError("CLOVA OCR 요청에는 data 또는 url이 필요합니다.")

        return {
            "version": "V2",
            "requestId": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "lang": "ko",
            "images": [image],
            "enableTableDetection": False,
        }

    def _post(self, payload: dict) -> dict:
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            self._rate_limiter.wait()
            try:
                response = self._client.post(
                    self.endpoint,
                    headers={
                        "X-OCR-SECRET": self.secret,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
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

            if not service_error.retryable or attempt == self.max_attempts:
                raise service_error from last_error
            time.sleep(min(0.5 * (2 ** (attempt - 1)), 2.0))

        raise OCRServiceError("CLOVA OCR 호출에 실패했습니다.") from last_error


def _infer_clova_format(suffix: str) -> str:
    image_format = suffix.lower().lstrip(".") or "jpg"
    if image_format == "jpeg":
        return "jpg"
    if image_format not in SUPPORTED_IMAGE_FORMATS:
        raise ValueError(f"지원하지 않는 OCR 이미지 형식입니다: {suffix or '(없음)'}")
    return image_format
