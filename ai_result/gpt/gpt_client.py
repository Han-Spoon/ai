import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()
load_dotenv(Path(__file__).parents[1] / ".env")

# 배포/버전은 .env 가 있으면 그 값을 쓰고, 없으면 ai_result 기본값으로 폴백한다.
_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-nano")
_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")


@lru_cache(maxsize=1)
def _get_client() -> AzureOpenAI:
    # 클라이언트를 지연 생성한다. 키가 없어도 import 단계에서 죽지 않고,
    # GPT 가 실제로 호출되는 케이스(unknown_menu/unknown_remain)에서만 실패한다.
    # 키 이름은 기존 .env(ai_ocr) 와 동일하게 AZURE_OPENAI_KEY 우선, API_KEY 폴백.
    return AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_KEY") or os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=_API_VERSION,
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    )


def ask_gpt_json(prompt: dict) -> dict:
    response = _get_client().chat.completions.create(
        model=_DEPLOYMENT,
        messages=[
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
    )
    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        raise ValueError(f"GPT 응답이 JSON이 아닙니다: {e}\n{response.choices[0].message.content}")
