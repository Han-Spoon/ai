"""⑧ cost measurement with a fake client (evaluation/xai_cost.py). No network."""

import asyncio
from types import SimpleNamespace

from ai_decision_policy import XaiLLM
from evaluation.cost import UsageMeter
from evaluation.xai_cost import measure, scan_estimate


class FakeClient:
    def __init__(self) -> None:
        self.responses = SimpleNamespace(parse=self._parse)

    async def _parse(self, **kwargs):
        schema = kwargs["text_format"]
        return SimpleNamespace(
            output_parsed=schema(reason="이 메뉴는 확인이 필요해요."),
            usage=SimpleNamespace(input_tokens=500, output_tokens=40),
        )


def test_every_language_runs_three_reasons_and_one_review():
    reason, review = asyncio.run(measure(XaiLLM(FakeClient(), "gpt-5.4-mini"), ["ko", "en"]))
    assert (reason.calls, review.calls) == (6, 2)
    assert reason.input_tokens == 3000 and review.output_tokens == 80


def test_scan_estimate_scales_the_per_call_tokens():
    reason = UsageMeter(calls=2, answered=2, input_tokens=1000, output_tokens=100)
    review = UsageMeter(calls=1, answered=1, input_tokens=800, output_tokens=60)
    scan = scan_estimate(reason, review, menus=10, share=0.5, reviews=3)
    # 5 reasons of 500/50 and 3 reviews of 800/60.
    assert (scan.calls, scan.input_tokens, scan.output_tokens) == (8, 4900, 430)
