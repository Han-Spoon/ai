"""Shared request-scoped concurrency policy for Normalization LLM calls."""

from __future__ import annotations

DEFAULT_MAX_CONCURRENT_LLM_CALLS = 10


def validate_max_concurrent_llm_calls(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= DEFAULT_MAX_CONCURRENT_LLM_CALLS
    ):
        raise ValueError("max concurrent LLM calls must be between 1 and 10")
    return value
