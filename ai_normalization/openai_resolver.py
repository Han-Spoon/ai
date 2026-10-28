"""OpenAI Responses API adapter for the two bounded Normalization decisions."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, TypeVar

from .contracts import (
    CandidateResolution,
    CandidateResolutionInput,
    NameResolution,
    NameResolutionInput,
)

DEFAULT_NORMALIZATION_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_NORMALIZATION_OPENAI_TIMEOUT_SECONDS = 5.0
DEFAULT_NORMALIZATION_OPENAI_MAX_RETRIES = 1
DEFAULT_NORMALIZATION_OPENAI_MAX_OUTPUT_TOKENS = 256

_ParsedModel = TypeVar("_ParsedModel", NameResolution, CandidateResolution)

_NAME_INSTRUCTIONS = """You normalize menu names from Korean restaurant OCR.
Return only the requested structured output.
Translate unresolved Latin menu words into a concise Korean search name.
Preserve every Hangul sequence and every residual token from the input.
Never add ingredients, options, prices, menu IDs, or safety conclusions.
If uncertain, keep normalized_menu_name unchanged and use confidence below 0.90."""

_CANDIDATE_INSTRUCTIONS = """You disambiguate one Korean restaurant menu from an allowed list.
Return only the requested structured output and select only a supplied menu_id.
Use only description, locale_hint, options, or residual_tokens as evidence.
The reason_code must name a context field that actually supports the choice.
Never infer ingredients, use outside knowledge, or make a safety decision.
If the context is insufficient, return confidence below 0.90."""


class NormalizationOpenAIConfigError(ValueError):
    """The optional OpenAI resolver environment is invalid."""


class NormalizationOpenAIResponseError(RuntimeError):
    """OpenAI returned no schema-valid parsed Normalization result."""


class OpenAINormalizationResolver:
    """One lifespan-scoped client implementing both Normalization resolvers."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_NORMALIZATION_OPENAI_MODEL,
        max_output_tokens: int = DEFAULT_NORMALIZATION_OPENAI_MAX_OUTPUT_TOKENS,
    ) -> None:
        if not model.strip():
            raise NormalizationOpenAIConfigError("normalization model must not be empty")
        if not 1 <= max_output_tokens <= 1024:
            raise NormalizationOpenAIConfigError(
                "normalization max output tokens must be between 1 and 1024"
            )
        self._client = client
        self.model = model
        self.max_output_tokens = max_output_tokens

    @classmethod
    def from_env(
        cls,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> "OpenAINormalizationResolver":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise NormalizationOpenAIConfigError("OPENAI_API_KEY is required")

        model = os.getenv(
            "NORMALIZATION_OPENAI_MODEL", DEFAULT_NORMALIZATION_OPENAI_MODEL
        ).strip()
        timeout = _float_env(
            "NORMALIZATION_OPENAI_TIMEOUT_SECONDS",
            DEFAULT_NORMALIZATION_OPENAI_TIMEOUT_SECONDS,
            minimum=0.1,
        )
        max_retries = _int_env(
            "NORMALIZATION_OPENAI_MAX_RETRIES",
            DEFAULT_NORMALIZATION_OPENAI_MAX_RETRIES,
            minimum=0,
            maximum=1,
        )
        max_output_tokens = _int_env(
            "NORMALIZATION_OPENAI_MAX_OUTPUT_TOKENS",
            DEFAULT_NORMALIZATION_OPENAI_MAX_OUTPUT_TOKENS,
            minimum=1,
            maximum=1024,
        )
        factory = client_factory or _async_openai_client
        client = factory(
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
        return cls(
            client,
            model=model,
            max_output_tokens=max_output_tokens,
        )

    @classmethod
    def from_env_if_configured(
        cls,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> "OpenAINormalizationResolver | None":
        if not os.getenv("OPENAI_API_KEY", "").strip():
            return None
        return cls.from_env(client_factory=client_factory)

    async def resolve_name(self, item: NameResolutionInput) -> NameResolution:
        return await self._parse(
            instructions=_NAME_INSTRUCTIONS,
            item=item,
            schema=NameResolution,
        )

    async def resolve_candidate(
        self, item: CandidateResolutionInput
    ) -> CandidateResolution:
        return await self._parse(
            instructions=_CANDIDATE_INSTRUCTIONS,
            item=item,
            schema=CandidateResolution,
        )

    async def _parse(
        self,
        *,
        instructions: str,
        item: Any,
        schema: type[_ParsedModel],
    ) -> _ParsedModel:
        response = await self._client.responses.parse(
            model=self.model,
            input=[
                {"role": "developer", "content": instructions},
                {
                    "role": "user",
                    "content": json.dumps(
                        item.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            text_format=schema,
            max_output_tokens=self.max_output_tokens,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise NormalizationOpenAIResponseError(
                "OpenAI returned no parsed normalization output"
            )
        if isinstance(parsed, schema):
            return parsed
        return schema.model_validate(parsed)


def _async_openai_client(**kwargs: Any) -> Any:
    try:
        from openai import AsyncOpenAI
    except ImportError as error:  # pragma: no cover - deployment dependency check
        raise NormalizationOpenAIConfigError(
            "openai package is required when OPENAI_API_KEY is configured"
        ) from error
    return AsyncOpenAI(**kwargs)


def _float_env(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as error:
        raise NormalizationOpenAIConfigError(f"{name} must be a number") from error
    if value < minimum:
        raise NormalizationOpenAIConfigError(f"{name} must be at least {minimum}")
    return value


def _int_env(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as error:
        raise NormalizationOpenAIConfigError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise NormalizationOpenAIConfigError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value
