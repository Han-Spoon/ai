import json
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

load_dotenv()
load_dotenv(Path(__file__).parents[1] / ".env")

# ai_ocr 과 같은 모델로 통일. (임시)
_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
_PLACEHOLDER_KEYS = {"PLACEHOLDER", "your_openai_api_key"}


class GPTServiceError(RuntimeError):
    """GPT 설정 또는 외부 호출 실패를 결과 파이프라인에 안전하게 전달한다."""


@dataclass
class _GPTRequestBudget:
    deadline: float
    max_calls: int
    calls: int = 0


_GPT_REQUEST_BUDGET: ContextVar[_GPTRequestBudget | None] = ContextVar(
    "gpt_request_budget", default=None
)


@contextmanager
def gpt_request_budget(*, total_seconds: float, max_calls: int) -> Iterator[None]:
    """한 번의 결과 생성에서 허용할 GPT 시간과 호출 수를 제한한다.

    ContextVar를 사용하므로 동시에 처리되는 FastAPI 요청끼리 예산을 공유하지 않는다.
    예산이 끝난 메뉴는 각 unknown 핸들러의 보수적 caution 폴백으로 처리된다.
    """
    if total_seconds <= 0:
        raise GPTServiceError("GPT 전체 시간 예산은 0보다 커야 합니다.")
    if max_calls < 0:
        raise GPTServiceError("GPT 최대 호출 수는 0 이상이어야 합니다.")

    token = _GPT_REQUEST_BUDGET.set(
        _GPTRequestBudget(
            deadline=time.monotonic() + total_seconds,
            max_calls=max_calls,
        )
    )
    try:
        yield
    finally:
        _GPT_REQUEST_BUDGET.reset(token)


def _claim_gpt_call() -> None:
    budget = _GPT_REQUEST_BUDGET.get()
    if budget is None:
        return
    if time.monotonic() >= budget.deadline:
        raise GPTServiceError("GPT 결과 보강 시간 예산이 종료되었습니다.")
    if budget.calls >= budget.max_calls:
        raise GPTServiceError("GPT 결과 보강 호출 예산이 종료되었습니다.")
    budget.calls += 1


def validate_openai_config() -> str:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key or api_key in _PLACEHOLDER_KEYS:
        raise GPTServiceError("OPENAI_API_KEY가 설정되지 않았습니다.")
    return api_key


@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    try:
        timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "5"))
    except ValueError as error:
        raise GPTServiceError("OPENAI_TIMEOUT_SECONDS가 올바르지 않습니다.") from error
    if timeout_seconds <= 0:
        raise GPTServiceError("OPENAI_TIMEOUT_SECONDS는 0보다 커야 합니다.")

    return OpenAI(
        api_key=validate_openai_config(),
        timeout=timeout_seconds,
        # 백엔드 result read timeout(7초) 안에서 폴백할 수 있도록 SDK 내부 재시도 금지.
        max_retries=0,
    )


def ask_gpt_json(prompt: dict) -> dict:
    try:
        # 네트워크 호출 직전에 예산을 선점해 한 요청의 연쇄 GPT 호출을 제한한다.
        _claim_gpt_call()
        response = _get_client().chat.completions.create(
            model=_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise GPTServiceError("GPT 응답이 비어 있습니다.")
        return json.loads(content)
    except GPTServiceError:
        raise
    except (OpenAIError, json.JSONDecodeError, IndexError, TypeError) as error:
        # API 키·프롬프트·응답 전문은 로그/클라이언트로 전파하지 않는다.
        raise GPTServiceError("GPT 결과 보강을 사용할 수 없습니다.") from error
