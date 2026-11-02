"""LLM usage counting for evaluations (evaluation/cost.py). No network."""

import asyncio
from types import SimpleNamespace

from evaluation.cost import MeteredClient, UsageMeter, cost_lines


class FakeClient:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.responses = SimpleNamespace(parse=self._parse)

    async def _parse(self, **kwargs):
        await asyncio.sleep(self.delay)
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


def test_answered_calls_add_their_usage():
    meter = UsageMeter()
    client = MeteredClient(FakeClient(), meter)
    asyncio.run(client.responses.parse(model="m"))
    asyncio.run(client.responses.parse(model="m"))
    assert meter.as_dict() == {"calls": 2, "answered": 2, "input_tokens": 2000, "output_tokens": 200}


def test_a_timed_out_call_is_counted_and_priced_at_the_average_input():
    meter = UsageMeter()
    client = MeteredClient(FakeClient(delay=0.2), meter)

    async def go():
        await MeteredClient(FakeClient(), meter).responses.parse(model="m")
        try:
            await asyncio.wait_for(client.responses.parse(model="m"), 0.01)
        except asyncio.TimeoutError:
            pass

    asyncio.run(go())
    assert (meter.calls, meter.answered, meter.unanswered) == (2, 1, 1)
    assert meter.estimated_input_tokens() == 2000
    # 2,000 input at 1 and 100 output at 10 per 1M tokens.
    assert meter.cost(1.0, 10.0) == (2000 * 1.0 + 100 * 10.0) / 1_000_000


def test_cost_lines_without_prices_say_how_to_get_them():
    meter = UsageMeter(calls=4, answered=4, input_tokens=4000, output_tokens=400)
    lines = cost_lines(meter, 2, "메뉴", None, None)
    assert "메뉴 하나당: 호출 2.0번" in lines[2]
    assert "--price-in" in lines[3]
    assert "전체 2.4, 메뉴 하나당 1.2" in cost_lines(meter, 2, "메뉴", 100.0, 5000.0)[3]
