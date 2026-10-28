from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai_normalization.contracts import (
    CandidateResolution,
    CandidateResolutionInput,
    NameResolution,
    NameResolutionInput,
)
from ai_normalization.openai_resolver import (
    DEFAULT_NORMALIZATION_OPENAI_MODEL,
    NormalizationOpenAIConfigError,
    NormalizationOpenAIResponseError,
    OpenAINormalizationResolver,
)


class FakeResponses:
    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.parsed)


class FakeClient:
    def __init__(self, parsed) -> None:
        self.responses = FakeResponses(parsed)


def test_name_resolution_uses_responses_structured_output() -> None:
    client = FakeClient(
        NameResolution(normalized_menu_name="타코", confidence=0.96)
    )
    resolver = OpenAINormalizationResolver(client)

    result = asyncio.run(
        resolver.resolve_name(
            NameResolutionInput(
                display_name="Taco",
                normalized_menu_name="taco",
                locale_hint="en",
                residual_tokens=[],
            )
        )
    )

    assert result.normalized_menu_name == "타코"
    call = client.responses.calls[0]
    assert call["model"] == DEFAULT_NORMALIZATION_OPENAI_MODEL
    assert call["text_format"] is NameResolution
    assert call["max_output_tokens"] == 256
    assert "Taco" in call["input"][1]["content"]


def test_candidate_resolution_uses_only_bounded_input_schema() -> None:
    client = FakeClient(
        CandidateResolution(
            selected_menu_id=1002,
            confidence=0.94,
            reason_code="description_supports_candidate",
        )
    )
    resolver = OpenAINormalizationResolver(client)

    result = asyncio.run(
        resolver.resolve_candidate(
            CandidateResolutionInput(
                raw_menu_name="김치전",
                normalized_menu_name="김치전",
                display_name="김치전",
                description="오징어가 들어간 해물전",
                locale_hint="ko",
                options=[],
                residual_tokens=[],
                candidates=[
                    {"menu_id": 1001, "canonical_name": "김치전"},
                    {"menu_id": 1002, "canonical_name": "해물김치전"},
                ],
            )
        )
    )

    assert result.selected_menu_id == 1002
    call = client.responses.calls[0]
    assert call["text_format"] is CandidateResolution
    assert "1002" in call["input"][1]["content"]
    assert "score" not in call["input"][1]["content"]


def test_missing_parsed_output_is_rejected() -> None:
    resolver = OpenAINormalizationResolver(FakeClient(None))

    with pytest.raises(
        NormalizationOpenAIResponseError,
        match="no parsed normalization output",
    ):
        asyncio.run(
            resolver.resolve_name(
                NameResolutionInput(
                    display_name="Taco",
                    normalized_menu_name="taco",
                    locale_hint="en",
                    residual_tokens=[],
                )
            )
        )


def test_from_env_requires_only_api_key_and_uses_safe_defaults(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("NORMALIZATION_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("NORMALIZATION_OPENAI_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("NORMALIZATION_OPENAI_MAX_RETRIES", raising=False)
    monkeypatch.delenv("NORMALIZATION_OPENAI_MAX_OUTPUT_TOKENS", raising=False)
    received = {}

    def factory(**kwargs):
        received.update(kwargs)
        return FakeClient(None)

    resolver = OpenAINormalizationResolver.from_env(client_factory=factory)

    assert resolver.model == DEFAULT_NORMALIZATION_OPENAI_MODEL
    assert resolver.max_output_tokens == 256
    assert received == {
        "api_key": "test-key",
        "timeout": 5.0,
        "max_retries": 1,
    }


def test_from_env_without_key_disables_optional_resolver(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert OpenAINormalizationResolver.from_env_if_configured() is None
    with pytest.raises(NormalizationOpenAIConfigError, match="OPENAI_API_KEY"):
        OpenAINormalizationResolver.from_env()


def test_retry_configuration_cannot_exceed_one(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("NORMALIZATION_OPENAI_MAX_RETRIES", "2")

    with pytest.raises(NormalizationOpenAIConfigError, match="between 0 and 1"):
        OpenAINormalizationResolver.from_env(
            client_factory=lambda **_: FakeClient(None)
        )
