"""LLM calls and tokens counted during an evaluation (⑥ §6, ⑧ §7-1).

``MeteredClient`` wraps an ``AsyncOpenAI`` client: ``responses.parse`` goes through
unchanged and the ``usage`` of each answer is added to a ``UsageMeter``. A call that is
cancelled by a time limit still reaches the gateway and may be billed, but its usage
never comes back; it is counted apart and priced at the average input of the answered
calls.

Prices are not in the code. Pass the gateway's price per 1M tokens (``--price-in``,
``--price-out``, in whatever currency the dashboard shows) to get a cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


@dataclass
class UsageMeter:
    calls: int = 0
    answered: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def unanswered(self) -> int:
        return self.calls - self.answered

    def estimated_input_tokens(self) -> float:
        """Answered input plus the average input for each call that got no answer."""

        if not self.answered:
            return float(self.input_tokens)
        return self.input_tokens + self.unanswered * self.input_tokens / self.answered

    def cost(self, price_in: float | None, price_out: float | None) -> float | None:
        if price_in is None or price_out is None:
            return None
        return (self.estimated_input_tokens() * price_in + self.output_tokens * price_out) / 1_000_000

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "answered": self.answered,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


class MeteredClient:
    """``AsyncOpenAI`` stand-in that counts every ``responses.parse`` call."""

    def __init__(self, client: Any, meter: UsageMeter) -> None:
        self._client = client
        self.meter = meter
        self.responses = SimpleNamespace(parse=self._parse)

    async def _parse(self, **kwargs: Any) -> Any:
        self.meter.calls += 1
        response = await self._client.responses.parse(**kwargs)
        usage = getattr(response, "usage", None)
        self.meter.answered += 1
        self.meter.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.meter.output_tokens += getattr(usage, "output_tokens", 0) or 0
        return response


def cost_lines(
    meter: UsageMeter,
    units: int,
    unit_name: str,
    price_in: float | None,
    price_out: float | None,
) -> list[str]:
    """Markdown lines: calls and tokens in total and per ``unit_name`` (메뉴, 스캔)."""

    per = max(units, 1)
    lines = [
        f"- LLM 호출 {meter.calls}번 (답 받음 {meter.answered}, 시간 제한으로 답 못 받음 {meter.unanswered})",
        f"- 입력 토큰 {meter.input_tokens:,} (답 못 받은 호출 포함 추정 {meter.estimated_input_tokens():,.0f}), 출력 토큰 {meter.output_tokens:,}",
        f"- {unit_name} 하나당: 호출 {meter.calls / per:.1f}번, 입력 {meter.estimated_input_tokens() / per:,.0f}·출력 {meter.output_tokens / per:,.0f} 토큰",
    ]
    total = meter.cost(price_in, price_out)
    if total is None:
        lines.append("- 비용: `--price-in`·`--price-out`(1M 토큰당 가격)을 주면 계산해요")
    else:
        lines.append(
            f"- 비용 (입력 {price_in:g}·출력 {price_out:g} / 1M 토큰): 전체 {total:,.4g}, {unit_name} 하나당 {total / per:,.4g}"
        )
    return lines
