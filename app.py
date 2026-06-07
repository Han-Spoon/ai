"""
한스푼 AI - FastAPI 얇은 래퍼

백엔드(Spring) AiClient 가 호출하는 내부 서비스용 HTTP 어댑터.
기존 ai_ocr / ai_ruleengine 로직을 그대로 재사용한다 (비즈니스 로직 중복 없음).

엔드포인트:
    POST /v1/ocr        : image_url 다운로드 → OCR 파이프라인 → 최종 dict
    POST /v1/ruleengine : ocr_result + profile → 위험도 판정 dict

실행:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Body, FastAPI, HTTPException
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

_result_spec = importlib.util.spec_from_file_location("ai_result_main", BASE_DIR / "ai_result" / "main.py")
_result_main = importlib.util.module_from_spec(_result_spec)
_result_spec.loader.exec_module(_result_main)
build_final_results_from_judged = _result_main.build_final_results_from_judged

# 룰엔진 진입 함수 (engine 모듈명은 고유라 충돌 없음)
from engine import analyze_all  # noqa: E402

app = FastAPI(title="Hanspoon AI", version="1.0.0")


# ── 요청 모델 ─────────────────────────────────────────────────────────────────
class OcrRequest(BaseModel):
    source: str | None = None
    storage_key: str | None = None
    image_url: str


class RuleEngineRequest(BaseModel):
    profile: dict
    ocr_result: dict


# ── 헬스체크 ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


# ── 1) OCR ────────────────────────────────────────────────────────────────────
@app.post("/v1/ocr")
def run_ocr(req: OcrRequest):
    """image_url 을 다운로드해 기존 OCR 파이프라인을 그대로 실행한다."""
    suffix = _infer_suffix(req.storage_key, req.image_url)

    tmp_path: str | None = None
    try:
        # 이미지 다운로드 (실패 시 502)
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(req.image_url)
                resp.raise_for_status()
                content = resp.content
                mime_type = _normalize_content_type(resp.headers.get("content-type"))
        except httpx.HTTPError as err:
            raise HTTPException(
                status_code=502,
                detail=f"이미지 다운로드 실패: {err}",
            ) from err

        if mime_type and not mime_type.startswith("image/"):
            raise HTTPException(
                status_code=415,
                detail=f"이미지 MIME 타입이 아닙니다: {mime_type}",
            )

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # 기존 OCR 파이프라인 실행. source/storage_key/image_url 그대로 전달.
        result = analyze_menu_image(
            tmp_path,
            source=req.source or "upload",
            storage_key=req.storage_key,
            image_url=req.image_url,
            mime_type=mime_type,
            file_size=len(content),
        )
        # build_final_result 가 만든 dict 를 그대로 반환
        return result["final"]
    except HTTPException:
        raise
    except Exception as err:  # OCR/GPT 등 파이프라인 오류 → 500
        raise HTTPException(status_code=500, detail=f"OCR 처리 실패: {err}") from err
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


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
        raise HTTPException(status_code=500, detail=f"결과 생성 실패: {err}") from err


def _infer_suffix(storage_key: str | None, image_url: str) -> str:
    """임시 파일 확장자 추론 (storage_key → URL 경로 → .jpg)."""
    if storage_key:
        ext = Path(storage_key).suffix
        if ext:
            return ext
    ext = Path(urlparse(image_url).path).suffix
    return ext or ".jpg"


def _normalize_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None
