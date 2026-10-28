"""OpenAI client settings shared by the two ⑧ LLM steps (docs/8-agent-xai.md §0-2, §2-3).

Same shape as ② ``ai_normalization/openai_resolver.py``: one lifespan-scoped
``AsyncOpenAI`` client, Responses API with a Pydantic output schema, and nothing at all
when ``OPENAI_API_KEY`` is unset. The SDK reads ``OPENAI_BASE_URL`` by itself, so a
gateway such as BAZE needs no code here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

DEFAULT_XAI_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_XAI_OPENAI_TIMEOUT_SECONDS = 5.0
# §0-2: the sentence LLM is retried once.
DEFAULT_XAI_OPENAI_MAX_RETRIES = 1

ParsedT = TypeVar("ParsedT", bound=BaseModel)


class XaiOpenAIConfigError(ValueError):
    """The optional ⑧ OpenAI environment is invalid."""


class XaiOpenAIResponseError(RuntimeError):
    """OpenAI returned no schema-valid parsed output."""


@dataclass(frozen=True)
class XaiLLM:
    client: Any
    model: str = DEFAULT_XAI_OPENAI_MODEL

    @classmethod
    def from_env_if_configured(
        cls, *, client_factory: Callable[..., Any] | None = None
    ) -> "XaiLLM | None":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        model = os.getenv("XAI_OPENAI_MODEL", DEFAULT_XAI_OPENAI_MODEL).strip()
        if not model:
            raise XaiOpenAIConfigError("XAI_OPENAI_MODEL must not be empty")
        timeout = _float_env("XAI_OPENAI_TIMEOUT_SECONDS", DEFAULT_XAI_OPENAI_TIMEOUT_SECONDS)
        retries = _int_env("XAI_OPENAI_MAX_RETRIES", DEFAULT_XAI_OPENAI_MAX_RETRIES)
        factory = client_factory or _async_openai_client
        return cls(factory(api_key=api_key, timeout=timeout, max_retries=retries), model)

    async def parse(
        self, instructions: str, payload: Any, schema: type[ParsedT], max_output_tokens: int
    ) -> ParsedT:
        response = await self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "developer", "content": instructions},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            text_format=schema,
            max_output_tokens=max_output_tokens,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise XaiOpenAIResponseError("OpenAI returned no parsed output")
        return parsed if isinstance(parsed, schema) else schema.model_validate(parsed)


def _async_openai_client(**kwargs: Any) -> Any:
    try:
        from openai import AsyncOpenAI
    except ImportError as error:  # pragma: no cover - deployment dependency check
        raise XaiOpenAIConfigError(
            "openai package is required when OPENAI_API_KEY is configured"
        ) from error
    return AsyncOpenAI(**kwargs)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as error:
        raise XaiOpenAIConfigError(f"{name} must be a number") from error
    if value < 0.1:
        raise XaiOpenAIConfigError(f"{name} must be at least 0.1")
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as error:
        raise XaiOpenAIConfigError(f"{name} must be an integer") from error
    if not 0 <= value <= 1:
        raise XaiOpenAIConfigError(f"{name} must be 0 or 1")
    return value
