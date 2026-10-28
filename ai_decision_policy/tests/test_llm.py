"""⑧ LLM steps with a fake client: the reason sentence and its #174 checks (§0-2, §4),
and the high-risk review (§2-3). No network."""

import asyncio

import pytest

from ai_decision_policy import DecisionTool, HighRiskReviewAgent, ReasonWriter, XaiLLM, check_reason, judge
from ai_decision_policy.reason import INSTRUCTIONS as REASON_INSTRUCTIONS
from ai_decision_policy.reason import ReasonOutput, instructions
from ai_decision_policy.review import ReviewOutput
from ai_decision_policy.tests.test_decision_policy import (
    warning,
    FISH_ALLERGY,
    HALAL,
    KIMCHI_STEW_CONFIRMED,
    PORK,
    SHRIMP_ALLERGY,
    probability,
    request,
)
from ai_supervisor.contracts.common import IngredientRef
from ai_supervisor.contracts.decision import HighRiskReviewRequest

SHRIMP = IngredientRef(ingredient_id=130, canonical_name="새우")
PEANUT = IngredientRef(ingredient_id=150, canonical_name="땅콩")
TOFU = IngredientRef(ingredient_id=160, canonical_name="두부")


class FakeLLM:
    """Stands in for ``XaiLLM``: returns ``output`` (or raises it) and records calls."""

    def __init__(self, output=None, *, delay: float = 0.0) -> None:
        self.output = output
        self.delay = delay
        self.calls: list[dict] = []

    async def parse(self, instructions, payload, schema, max_output_tokens):
        self.calls.append({"instructions": instructions, "payload": payload, "schema": schema})
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def check(text, *, locale="ko", risk_level="caution", percents=(62,), names=("돼지고기",)):
    return check_reason(
        text,
        locale=locale,
        risk_level=risk_level,
        percents=percents,
        allowed_names=names,
        menu_names=["김치찌개"],
    )


# --- #174 checks --------------------------------------------------------------


def test_reason_check_keeps_a_faithful_sentence() -> None:
    text = "김치찌개 레시피 대부분에 돼지고기가 들어가서 약 62%로 나왔어요."
    assert check(text) == text


@pytest.mark.parametrize("text", ["돼지고기가 들어 있을 확률은 약 63%예요.", "돼지고기가 100% 들어 있어요.", "돼지고기는 0%예요."])
def test_reason_check_drops_a_percent_the_verdict_does_not_show(text) -> None:
    assert check(text) is None


@pytest.mark.parametrize(
    ("locale", "text"),
    [
        ("ja", "豚肉が入っている確率は約80％です。"),
        ("zh-CN", "可能含有猪肉，概率约为８０%。"),
        ("zh-CN", "约百分之八十的食谱含有猪肉。"),
        ("en", "There is an 80 percent chance of pork."),
        ("ko", "돼지고기가 80퍼센트 확률로 들어 있어요."),
        ("es", "Hay un 80 por ciento de probabilidad de cerdo."),
    ],
)
def test_reason_check_reads_full_width_and_word_percentages(locale, text) -> None:
    # A wrong number must not slip through as "８０%", "80％", "80 percent" or 百分之.
    names = ("돼지고기", "pork", "豚肉", "猪肉", "cerdo")
    assert check(text, locale=locale, names=names) is None


@pytest.mark.parametrize(
    ("locale", "text"),
    [
        ("ja", "豚肉は約62％のレシピに入っています。"),
        ("zh-CN", "约百分之62的食谱含有猪肉。"),
        ("en", "About 62 percent of recipes use pork."),
        ("ar", "يوجد لحم الخنزير في حوالي ٦٢٪ من الوصفات."),
    ],
)
def test_reason_check_keeps_the_shown_percentage_in_any_form(locale, text) -> None:
    names = ("pork", "豚肉", "猪肉", "لحم الخنزير")
    assert check(text, locale=locale, names=names) is not None


def test_reason_check_drops_a_safe_claim_on_caution() -> None:
    assert check("돼지고기가 적게 들어가서 안전해요.") is None
    assert check("Pork is rare here, so it is safe to eat.", locale="en", names=("pork",)) is None


def test_reason_check_drops_an_ingredient_the_request_did_not_carry() -> None:
    assert check("돼지고기 대신 새우가 들어갈 수도 있어요.") is None
    assert check("It may contain pork or shrimp.", locale="en", names=("pork",)) is None


def test_reason_check_reads_menu_name_and_whole_words_only() -> None:
    # "김치" inside the menu name and "고기" inside "돼지고기" are not new names;
    # "pineapple" does not contain the word "apple".
    assert check("김치찌개에는 보통 돼지고기를 넣어요.") is not None
    assert check("Most recipes add pork with pineapple.", locale="en", names=("pork", "pineapple")) is not None


def test_reason_check_drops_the_wrong_language() -> None:
    assert check("이 메뉴에는 돼지고기가 들어갈 수 있어요.", locale="en", names=("pork", "돼지고기")) is None
    # An untranslated web ingredient may stay in Korean (§4).
    assert check("This dish may contain 묵은지 pork.", locale="en", names=("pork", "묵은지")) is not None


def test_reason_check_cuts_a_copy_of_the_verdict() -> None:
    verdict = "돼지고기(약 62%)가 들어 있을 수 있어요."
    kwargs = dict(locale="ko", risk_level="caution", percents=[62], allowed_names=["돼지고기"], verdict=verdict)
    assert check_reason(f"{verdict} 국물에 넣는 경우가 많아요.", **kwargs) == "국물에 넣는 경우가 많아요."
    assert check_reason(verdict, **kwargs) is None


def test_reason_check_reads_korean_names_only_at_the_start_of_a_word() -> None:
    # "분이라면" is not 라면 (ramen); a real "라면을" still is.
    assert check("돼지고기를 피하시는 분이라면 주의해 주세요.") is not None
    assert check("돼지고기와 라면을 같이 먹는 경우가 많아요.") is None


def test_reason_check_drops_the_whole_sentence_that_repeats_the_verdict() -> None:
    verdict = "참치가 들어 있어요."
    text = "이 요리에는 참치가 들어 있어요. 참치는 주재료라 빼기 어려워요."
    kwargs = dict(locale="ko", risk_level="danger", percents=[], allowed_names=["참치"], verdict=verdict)
    assert check_reason(text, **kwargs) == "참치는 주재료라 빼기 어려워요."


def test_reason_may_name_the_users_restriction() -> None:
    # "if you avoid fish" names the restriction, not an ingredient the request lacks.
    fish_sauce = IngredientRef(ingredient_id=110, canonical_name="액젓")
    for locale, text in (
        ("ko", "액젓은 숨은 재료라서 생선을 피하시면 확인이 필요해요."),
        ("en", "This dish may contain 액젓, which can be made with fish."),
    ):
        req = request(FISH_ALLERGY, probability_results=[probability(fish_sauce, ["is_fish"], 0.8, role="hidden")], locale=locale)
        llm = FakeLLM(ReasonOutput(reason=text))
        assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason == text


def test_reason_check_trims_a_doubled_korean_ending() -> None:
    text = "추정 결과라서 단정할 수 없어요요. 레시피도 몇 개뿐이라 덜 확실해요요."
    assert check(text) == "추정 결과라서 단정할 수 없어요. 레시피도 몇 개뿐이라 덜 확실해요."


def test_korean_style_rule_goes_only_to_korean() -> None:
    # Told to every language, the model put "요" after Chinese sentences.
    assert "해요체" in instructions(REASON_INSTRUCTIONS, "ko")
    assert "해요체" not in instructions(REASON_INSTRUCTIONS, "zh-TW")
    assert "Traditional Chinese" in instructions(REASON_INSTRUCTIONS, "zh-TW")


def test_reason_check_drops_empty_or_long_text() -> None:
    assert check("   ") is None
    assert check("돼지고기 " * 80) is None


# --- reason sentence on the decision node ---------------------------------------


def estimated_pork(locale="ko"):
    return request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.62)], locale=locale)


def test_decision_tool_adds_a_checked_reason_and_keeps_the_verdict() -> None:
    llm = FakeLLM(ReasonOutput(reason="김치찌개는 돼지고기를 넣는 레시피가 많아요."))
    req = estimated_pork()
    response = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    plain = judge(req)
    assert response.message.reason == "김치찌개는 돼지고기를 넣는 레시피가 많아요."
    assert response.model_dump(exclude={"message": {"reason"}}) == plain.model_dump(exclude={"message": {"reason"}})
    payload = llm.calls[0]["payload"]
    assert payload["verdict_code"] == {"risk_level": "caution", "evidence_basis": "estimated"}
    assert payload["risk_ingredients"][0]["percent"] == "62%"
    assert "Korean" in llm.calls[0]["instructions"]


@pytest.mark.parametrize(
    "llm",
    [
        FakeLLM(RuntimeError("gateway down")),
        FakeLLM(ReasonOutput(reason="돼지고기가 들어 있을 확률은 약 80%예요.")),
        FakeLLM(ReasonOutput(reason="늦은 답")),
    ],
)
def test_decision_tool_falls_back_to_no_reason(llm) -> None:
    if llm.output and getattr(llm.output, "reason", "") == "늦은 답":
        llm.delay = 0.2
    writer = ReasonWriter(llm, timeout_seconds=0.05)
    response = asyncio.run(DecisionTool(writer).run(estimated_pork()))
    assert response.message.reason is None
    assert response == judge(estimated_pork())


def test_decision_tool_skips_the_llm_when_nothing_drives_the_verdict() -> None:
    llm = FakeLLM(ReasonOutput(reason="unused"))
    safe = request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, locale="ko")
    no_info = request(HALAL, no_information=True, locale="ko")
    for req in (safe, no_info):
        assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None
    assert llm.calls == []


def test_known_limits_reach_the_llm_as_plain_facts() -> None:
    # Warnings and information_status become plain facts; codes never reach the LLM.
    llm = FakeLLM(ReasonOutput(reason="웹에서 찾은 레시피가 적어서 더 불확실해요."))
    req = request(
        HALAL,
        probability_results=[probability(PORK, ["is_pork"], 0.62)],
        warnings=[warning("insufficient_sources"), warning("insufficient_sources"), warning("some_internal_code")],
        information_status="partial",
        locale="ko",
    )
    response = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    assert llm.calls[0]["payload"]["known_limits"] == [
        "Only a few web recipes were found for this dish.",
        "Part of the ingredient information could not be loaded.",
    ]
    assert "insufficient_sources" not in str(llm.calls[0]["payload"])
    assert response.message.reason == "웹에서 찾은 레시피가 적어서 더 불확실해요."


def test_known_limits_alone_are_worth_a_reason() -> None:
    # No ingredient hits the user, but the result is uncertain for a reason the user
    # can understand: the LLM explains it. No information at all, or safe, still skips.
    llm = FakeLLM(ReasonOutput(reason="재료 정보 일부를 불러오지 못해서 확실하지 않아요."))
    uncertain = request(HALAL, probability_results=[probability(TOFU, [], 0.8)], information_status="partial", locale="ko")
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(uncertain)).message.reason is not None
    llm.calls.clear()
    no_info = request(HALAL, no_information=True, information_status="none", locale="ko")
    safe = request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, locale="ko")
    for req in (no_info, safe):
        assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None
    assert llm.calls == []


def test_without_api_key_everything_stays_rule_only(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert XaiLLM.from_env_if_configured() is None
    assert DecisionTool.from_env().reason_writer is None
    assert HighRiskReviewAgent.from_env().llm is None


def test_api_key_builds_one_client_with_env_settings(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("XAI_OPENAI_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("XAI_OPENAI_TIMEOUT_SECONDS", "4")
    seen = {}
    llm = XaiLLM.from_env_if_configured(client_factory=lambda **kw: seen.update(kw) or "client")
    assert (llm.client, llm.model) == ("client", "gpt-5.4-mini")
    assert seen == {"api_key": "test-key", "timeout": 4.0, "max_retries": 1}


# --- high-risk review -------------------------------------------------------------


def review_request(*, user=SHRIMP_ALLERGY, reason="이 메뉴의 재료 정보가 부족해요.", locale="ko") -> HighRiskReviewRequest:
    decision = judge(request(user, probability_results=[probability(TOFU, [], 0.8)], locale=locale))
    return HighRiskReviewRequest(
        context=decision.context,
        menu={
            "menu_id": 1001,
            "normalized_menu_name": "김치찌개",
            "raw_menu_name": "김치찌개",
            "description": None,
        },
        reason_code="partial_information",
        decision={
            "risk_level": decision.risk_level,
            "evidence_basis": decision.evidence_basis,
            "message": decision.message.model_copy(update={"reason": reason}),
            "owner_cards": decision.owner_cards,
        },
        found_ingredients=[{"ingredient": TOFU, "constraint_tags": []}],
        ingredient_choices=[
            {"ingredient_id": 130, "canonical_name": "새우", "localized_name": "새우", "constraint_tags": ["is_shrimp"]},
            {"ingredient_id": 150, "canonical_name": "땅콩", "localized_name": "땅콩", "constraint_tags": ["is_peanut"]},
            {"ingredient_id": 101, "canonical_name": "돼지고기", "localized_name": "돼지고기", "constraint_tags": ["is_pork"]},
        ],
        user_profile=user,
        locale=locale,
        warnings=[],
        errors=[],
    )


def test_review_without_llm_changes_nothing() -> None:
    req = review_request()
    response = asyncio.run(HighRiskReviewAgent(None).run(req))
    assert response.missed_ingredients == []
    assert response.owner_cards == req.decision.owner_cards
    assert response.reason == "이 메뉴의 재료 정보가 부족해요."


def test_review_asks_about_missed_ingredients_that_hit_the_user() -> None:
    llm = FakeLLM(ReviewOutput(missed_ingredient_ids=[130, 130, 999, 101], reason="김치찌개에는 새우젓 때문에 새우가 들어갈 수 있어요."))
    req = review_request()
    response = asyncio.run(HighRiskReviewAgent(llm).run(req))

    # Only shrimp: 999 is not a choice, pork does not hit a shrimp allergy, duplicates drop.
    assert response.missed_ingredients == [SHRIMP]
    new = response.owner_cards[len(req.decision.owner_cards):]
    assert [(c.ingredient, c.reason_codes, c.card_id) for c in new] == [
        (SHRIMP, ["high_risk_review"], "card:scan-123:0:ingredient-130")
    ]
    assert new[0].question["ko"]
    assert response.owner_cards[: len(req.decision.owner_cards)] == req.decision.owner_cards
    assert response.reason == "김치찌개에는 새우젓 때문에 새우가 들어갈 수 있어요."
    # The prompt only lists choices that can hit this user.
    assert [c["id"] for c in llm.calls[0]["payload"]["ingredient_choices"]] == [130]


def test_review_keeps_the_old_reason_when_the_new_one_fails_the_check() -> None:
    llm = FakeLLM(ReviewOutput(missed_ingredient_ids=[], reason="새우는 없어서 안전해요."))
    response = asyncio.run(HighRiskReviewAgent(llm).run(review_request()))
    assert response.reason == "이 메뉴의 재료 정보가 부족해요."


def test_review_falls_back_on_llm_error() -> None:
    req = review_request()
    response = asyncio.run(HighRiskReviewAgent(FakeLLM(RuntimeError("down"))).run(req))
    assert response.owner_cards == req.decision.owner_cards
    assert response.missed_ingredients == []
