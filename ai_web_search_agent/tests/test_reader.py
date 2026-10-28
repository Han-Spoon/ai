"""⑥ OpenAI recipe reader with a fake client (§2-1 step 2, §3-0). No network."""

import asyncio
from types import SimpleNamespace

import pytest

from ai_web_search_agent import OpenAIRecipeReader, StaticSearchProvider, WebCollectAgent
from ai_web_search_agent.reader import _Reading, _Readings
from ai_web_search_agent.tests.test_collect import QUERY, request, result

REVIEW = "강남 김치찌개 맛집 후기. 국물이 진하고 친절해요."


class FakeClient:
    """``AsyncOpenAI`` stand-in: ``responses.parse`` returns ``parsed`` or raises it."""

    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.calls: list[dict] = []
        self.responses = SimpleNamespace(parse=self._parse)

    async def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.parsed, Exception):
            raise self.parsed
        return SimpleNamespace(output_parsed=self.parsed)


def readings(*items: tuple[int, bool, list[str]]) -> _Readings:
    return _Readings(readings=[_Reading(index=i, is_menu_recipe=k, ingredients=n) for i, k, n in items])


def test_reader_returns_one_reading_per_result_in_order() -> None:
    client = FakeClient(readings((1, False, []), (0, True, ["돼지고기", " 묵은지 ", ""])))
    out = asyncio.run(OpenAIRecipeReader(client).read("김치찌개", [result(1), result(2, REVIEW)]))
    assert [(r.is_menu_recipe, r.ingredients) for r in out] == [(True, ["돼지고기", "묵은지"]), (False, [])]
    sent = client.calls[0]
    assert sent["model"] == "gpt-5.4-mini"
    assert "김치찌개" in sent["input"][1]["content"]


@pytest.mark.parametrize(
    "parsed",
    [
        readings((0, True, [])),  # one result missing
        readings((0, True, []), (0, True, []), (1, True, [])),  # duplicate index
        RuntimeError("gateway down"),
        None,
    ],
)
def test_reader_raises_on_a_bad_answer(parsed) -> None:
    with pytest.raises(Exception):
        asyncio.run(OpenAIRecipeReader(FakeClient(parsed)).read("김치찌개", [result(1), result(2)]))


def test_collect_uses_the_reader_and_still_checks_the_text() -> None:
    # Result 2 is a review the LLM drops; "묵은지" is kept because the text has it,
    # "설탕" is dropped because no result text says so (§3-0).
    results = [result(1, "재료: 돼지고기, 묵은지, 두부"), result(2, REVIEW)]
    client = FakeClient(readings((0, True, ["묵은지", "설탕"]), (1, False, [])))
    agent = WebCollectAgent(StaticSearchProvider({QUERY: results}), OpenAIRecipeReader(client))
    response = asyncio.run(agent.run(request()))
    assert [s.source_url for s in response.sources] == [results[0].url]
    names = [i.canonical_name for i in response.sources[0].ingredients]
    assert "묵은지" in names and "설탕" not in names
    assert not any(w.code == "web_llm_extraction_failed" for w in response.warnings)


def test_collect_keeps_everything_when_the_reader_fails() -> None:
    results = [result(1), result(2, REVIEW + " 돼지고기")]
    agent = WebCollectAgent(
        StaticSearchProvider({QUERY: results}), OpenAIRecipeReader(FakeClient(RuntimeError("down")))
    )
    response = asyncio.run(agent.run(request()))
    assert len(response.sources) == 2
    assert any(w.code == "web_llm_extraction_failed" and w.forces_caution for w in response.warnings)


def test_from_env(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert OpenAIRecipeReader.from_env_if_configured() is None
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WEB_SEARCH_OPENAI_MODEL", "gpt-5.4-nano")
    seen = {}
    reader = OpenAIRecipeReader.from_env_if_configured(client_factory=lambda **kw: seen.update(kw) or "c")
    assert (reader.client, reader.model) == ("c", "gpt-5.4-nano")
    assert seen == {"api_key": "test-key", "timeout": 3.0, "max_retries": 0}
