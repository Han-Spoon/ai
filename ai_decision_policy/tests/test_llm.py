"""⑧ LLM steps with a fake client: the reason sentence and its #174 checks (§0-2, §4),
and the high-risk review (§2-3). No network."""

import asyncio

import pytest

from ai_decision_policy import DecisionTool, HighRiskReviewAgent, ReasonWriter, XaiLLM, check_reason, judge
from ai_decision_policy.reason import INSTRUCTIONS as REASON_INSTRUCTIONS
from ai_decision_policy.reason import MAX_REASON_CHARS, ReasonOutput, instructions
from ai_decision_policy.review import MissedIngredient, ReviewOutput
from ai_decision_policy.tests.test_decision_policy import (
    warning,
    FISH_ALLERGY,
    HALAL,
    KIMCHI_STEW_CONFIRMED,
    PORK,
    SHRIMP_ALLERGY,
    probability,
    profile,
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 2026-11-22 순두부찌개: a bare "%" after the last sentence.
        ("돼지고기가 들어갈 수 있어서 할랄 기준에 맞지 않아요. %", "돼지고기가 들어갈 수 있어서 할랄 기준에 맞지 않아요."),
        ("돼지고기가 % 들어갈 수 있어요.", "돼지고기가 들어갈 수 있어요."),
        ("돼지고기가 들어갈 확률은 62 %예요.", "돼지고기가 들어갈 확률은 62 %예요."),
    ],
)
def test_reason_check_drops_a_percent_sign_with_no_number(text, expected) -> None:
    assert check(text) == expected


def test_reason_check_ends_korean_sentences_with_a_period() -> None:
    # 2026-11-22: "…빼기 어려울 수 있어요; 까나리는 …".
    assert check("돼지고기가 들어갈 수 있어요; 할랄 기준에 맞지 않아요.") == (
        "돼지고기가 들어갈 수 있어요. 할랄 기준에 맞지 않아요."
    )


def test_reason_check_splits_korean_sentences_joined_by_a_comma() -> None:
    # 2026-11-22: "…들어갈 수 있어요, 그리고 …".
    assert check("돼지고기가 들어갈 수 있어요, 그리고 할랄 기준에 맞지 않아요.") == (
        "돼지고기가 들어갈 수 있어요. 그리고 할랄 기준에 맞지 않아요."
    )


def test_reason_check_reads_yeoreo_gaji_as_kinds_not_eggplant() -> None:
    # 2026-11-22: "여러 가지" dropped two vegan reasons as naming 가지 (eggplant).
    text = "돼지고기 말고도 여러 가지 재료가 들어갈 수 있어요. 두 가지 모두 할랄 기준에 맞지 않아요."
    assert check(text) == text
    assert check("돼지고기와 가지가 들어갈 수 있어요.") is None


def test_reason_check_drops_a_reason_that_is_only_a_percent_sign() -> None:
    assert check("%") is None


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


def test_reason_check_cuts_a_verdict_copy_that_differs_only_in_punctuation() -> None:
    # 2026-11-12 돼지불백: the reason repeated the verdict with one more comma.
    verdict = "We still need to check for blue crab, napa cabbage kimchi, yogurt and cheese."
    text = (
        "We still need to check for blue crab, napa cabbage kimchi, yogurt, and cheese, and "
        "these may be in this dish. Part of the information could not be loaded."
    )
    kwargs = dict(
        locale="en", risk_level="caution", percents=[],
        allowed_names=["blue crab", "napa cabbage kimchi", "yogurt", "cheese"], verdict=verdict,
    )
    assert check_reason(text, **kwargs) == "Part of the information could not be loaded."


@pytest.mark.parametrize(
    ("locale", "text"),
    [
        ("en", "This dish already includes blue crab."),
        ("ko", "이 음식에는 꽃게가 이미 들어 있어요."),
        ("ja", "ワタリガニがすでに入っています。"),
    ],
)
def test_reason_check_drops_an_estimate_told_as_fact(locale, text) -> None:
    # 2026-11-12 꽃게탕: "already includes" for an estimated ingredient.
    names = ["blue crab", "꽃게", "ワタリガニ"]
    kwargs = dict(locale=locale, risk_level="caution", percents=[], allowed_names=names)
    assert check_reason(text, evidence_basis="estimated", **kwargs) is None
    # The same words are fine once the owner confirmed it.
    assert check_reason(text, evidence_basis="confirmed", **kwargs) == text


def test_reason_check_keeps_a_possibility_and_a_hedge() -> None:
    kwargs = dict(locale="ko", risk_level="caution", percents=[], allowed_names=["꽃게"], evidence_basis="estimated")
    text = "꽃게가 들어갈 수 있어요. 아직 확실히 알 수는 없어요."
    assert check_reason(text, **kwargs) == text


def test_reason_keeps_korean_only_for_names_given_in_korean() -> None:
    # 2026-11-17 scenario 5: "This dish can still contain 양념" passed in English though
    # 양념 went to the LLM as "seasoning sauce".
    sauce = IngredientRef(ingredient_id=170, canonical_name="양념")
    soy = profile(allergies=["is_soybean"])
    req = request(soy, probability_results=[probability(sauce, ["is_soybean"], 0.29)], locale="en")
    text = "This dish can still contain 양념, which may include soybean-based ingredients."
    assert asyncio.run(DecisionTool(ReasonWriter(FakeLLM(ReasonOutput(reason=text)))).run(req)).message.reason is None
    # A name given only in Korean may stay in Korean.
    kwargs = dict(locale="en", risk_level="caution", percents=[], allowed_names={"까나리액젓"}, menu_names=[])
    kept = "This dish may contain 까나리액젓."
    assert check_reason(kept, korean_names=["까나리액젓"], **kwargs) == kept
    assert check_reason(kept, korean_names=[], **kwargs) is None


@pytest.mark.parametrize("text", [
    "This dish already lists several soybean-based ingredients, so there is no additional soybean item that clearly needs to be flagged.",
    "There is nothing to worry about beyond the soy sauce.",
])
def test_caution_reason_may_not_sound_reassuring(text) -> None:
    assert check(text, locale="en", percents=(), names={"soy sauce", "간장"}) is None


def test_reason_may_name_the_users_restriction() -> None:
    # "if you avoid fish" names the restriction, not an ingredient the request lacks.
    fish_sauce = IngredientRef(ingredient_id=110, canonical_name="액젓")
    for locale, text in (
        ("ko", "액젓은 숨은 재료라서 생선을 피하시면 확인이 필요해요."),
        ("en", "This dish may contain 액젓, which can be made with fish."),
    ):
        req = request(FISH_ALLERGY, probability_results=[probability(fish_sauce, ["is_fish"], 0.8, role="hidden")], locale=locale)
        llm = FakeLLM(ReasonOutput(reason=text))
        # The fixed tail (card, "80% 이상") follows the LLM text since 2026-11-22.
        assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason.startswith(text)


def test_reason_may_translate_a_name_given_only_in_korean() -> None:
    # 까나리액젓 has no English name yet; the LLM translates it and says so (§4).
    sauce = IngredientRef(ingredient_id=None, canonical_name="까나리액젓")
    req = request(
        HALAL,
        probability_results=[probability(PORK, ["is_pork"], 0.62), probability(sauce, [], 0.5)],
        localized_names={PORK.ingredient_id: "pork"},
        warnings=[warning("insufficient_sources")],
    )
    text = "Pork is a main ingredient here, and sand lance fish sauce is used for seasoning."
    told = ReasonOutput(name_translations=[{"korean": "까나리액젓", "translated": "sand lance fish sauce"}], reason=text)
    llm = FakeLLM(told)
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason.startswith(text)
    assert "translate it into English" in llm.calls[0]["instructions"]
    # Without the translation listed, "fish sauce" reads as 피쉬소스, a name it did not carry.
    silent = FakeLLM(ReasonOutput(reason=text))
    assert asyncio.run(DecisionTool(ReasonWriter(silent)).run(req)).message.reason is None


def test_a_translation_only_counts_for_a_name_given_in_korean() -> None:
    # Pork already has an English name; "translating" it as shrimp adds shrimp.
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.62)], localized_names={PORK.ingredient_id: "pork"})
    told = ReasonOutput(name_translations=[{"korean": "돼지고기", "translated": "shrimp"}], reason="Pork or shrimp may be in this dish.")
    assert asyncio.run(DecisionTool(ReasonWriter(FakeLLM(told))).run(req)).message.reason is None


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
    assert check("돼지고기 " * (MAX_REASON_CHARS // 5 + 1)) is None


# --- reason sentence on the decision node ---------------------------------------


# 삼겹살 does not say "pork" by its name, so it is worth a reason (needs_reason).
PORK_BELLY = IngredientRef(ingredient_id=105, canonical_name="삼겹살")


def estimated_pork(locale="ko"):
    return request(HALAL, probability_results=[probability(PORK_BELLY, ["is_pork"], 0.62)], locale=locale)


def test_no_reason_when_the_name_already_says_why() -> None:
    # "돼지고기 (약 62%)" for a halal user: a reason could only repeat the verdict.
    llm = FakeLLM(ReasonOutput(reason="unused"))
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.62)], locale="ko")
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None
    assert llm.calls == []
    # The same menu with a known limit still gets one.
    limited = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.62)], locale="ko",
                      warnings=[warning("insufficient_sources")])
    llm = FakeLLM(ReasonOutput(reason="웹에서 찾은 레시피가 적어서 덜 확실해요."))
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(limited)).message.reason is not None


def test_decision_tool_adds_a_checked_reason_and_keeps_the_verdict() -> None:
    llm = FakeLLM(ReasonOutput(reason="김치찌개는 돼지고기를 넣는 레시피가 많아요."))
    req = estimated_pork()
    response = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    plain = judge(req)
    assert response.message.reason.startswith("김치찌개는 돼지고기를 넣는 레시피가 많아요.")
    assert response.model_dump(exclude={"message": {"reason"}}) == plain.model_dump(exclude={"message": {"reason"}})
    payload = llm.calls[0]["payload"]
    assert payload["verdict_code"] == {"risk_level": "caution", "evidence_basis": "estimated"}
    # Since 2026-11-22 only the groups to explain go in: no percent, no full list.
    assert "risk_ingredients" not in payload and "explain_groups" in payload
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
    # partial here comes from the web warning, which says what happened itself; "could
    # not be loaded" would be untrue (2026-11-17).
    assert llm.calls[0]["payload"]["known_limits"] == [
        "Only a few web recipes were found for this dish.",
    ]
    assert "insufficient_sources" not in str(llm.calls[0]["payload"])
    assert response.message.reason.startswith("웹에서 찾은 레시피가 적어서 더 불확실해요.")


def test_reason_payload_names_restrictions_in_the_users_language() -> None:
    kimchi = IngredientRef(ingredient_id=109, canonical_name="배추김치")
    llm = FakeLLM(ReasonOutput(reason="Napa cabbage kimchi may contain shrimp."))
    req = request(SHRIMP_ALLERGY, probability_results=[probability(kimchi, ["is_shrimp"], 0.86)], locale="en")
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    assert llm.calls[0]["payload"]["explain_groups"] == [
        {"avoided_in_them": ["shrimp"], "ingredients": [
            {"name": "napa cabbage kimchi", "korean_name": "배추김치", "role": None, "hard_to_remove": False}]}
    ]


def test_reason_payload_keeps_what_is_avoided_apart_from_the_users_restriction() -> None:
    # 2026-11-22: a vegan's 어묵 read "생선 제한" when the tag name was the only field.
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    fish_cake = IngredientRef(ingredient_id=201, canonical_name="어묵")
    llm = FakeLLM(ReasonOutput(reason="어묵은 생선으로 만들어서 비건 기준에 맞지 않을 수 있어요."))
    req = request(vegan, probability_results=[probability(fish_cake, ["is_fish"], 0.46)], locale="ko")
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    group = llm.calls[0]["payload"]["explain_groups"][0]
    assert group["avoided_in_them"] == ["생선"]
    # The verdict names 비건; "…비건 기준에 맞지 않아요" in the reason only added words.
    assert "goes_against_user" not in group


def test_reason_payload_leaves_likelihood_to_the_tail() -> None:
    # 2026-11-22: no percent and no "자주" from the LLM; the fixed tail says those.
    anchovy_sauce = IngredientRef(ingredient_id=None, canonical_name="멸치액젓")
    ham = IngredientRef(ingredient_id=None, canonical_name="햄")
    sauce = IngredientRef(ingredient_id=None, canonical_name="굴소스")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    llm = FakeLLM(ReasonOutput(reason="멸치액젓은 멸치를 발효해 만든 액젓이에요."))
    req = request(
        vegan,
        probability_results=[
            probability(anchovy_sauce, ["is_fish"], 0.96, role="hidden"),
            probability(ham, ["is_pork"], 0.6, role="component"),
            probability(sauce, ["is_shellfish"], 0.1, role="component"),
            probability(SHRIMP, ["is_shrimp"], 0.21, role="component"),
        ],
        locale="ko",
    )
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    groups = llm.calls[0]["payload"]["explain_groups"]
    rows = {i["korean_name"]: i["hard_to_remove"] for g in groups for i in g["ingredients"]}
    # 새우 shows its cause by name, so the verdict covers it.
    assert rows == {"멸치액젓": True, "햄": False, "굴소스": False}
    # How often and how likely are the fixed tail's, not the LLM's (2026-11-22).
    assert all("percent" not in i and "how_often" not in i for g in groups for i in g["ingredients"])


def test_reason_ends_with_the_fixed_tail() -> None:
    # 2026-11-22: the "80% 이상" sentence and the card step are templates after the LLM text.
    anchovy_sauce = IngredientRef(ingredient_id=None, canonical_name="멸치액젓")
    ham = IngredientRef(ingredient_id=None, canonical_name="햄")
    sausage = IngredientRef(ingredient_id=None, canonical_name="소시지")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    text = "멸치액젓은 생선을 발효해 만든 액젓이라 생선이 들어갈 수 있어요."
    llm = FakeLLM(ReasonOutput(reason=text))
    req = request(
        vegan,
        probability_results=[
            probability(anchovy_sauce, ["is_fish"], 0.96, role="hidden"),
            probability(ham, ["is_pork"], 0.9, role="component"),
            probability(sausage, ["is_pork"], 0.3, role="component"),
        ],
        locale="ko",
    )
    reason = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason
    assert reason == (
        text
        + " 멸치액젓, 햄은 한스푼이 수집한 레시피의 80% 이상에 들어가는 재료라서 특히 주의해야 해요."
        # No ingredient names in the card step: "사골은 빼고 요청 카드로…" (2026-11-22).
        + " 빼고 요청 카드로 빼 달라고 부탁하거나, 질문 카드로 직원에게 직접 확인해 보세요."
    )


def test_reason_tail_is_not_sent_without_a_checked_reason() -> None:
    ham = IngredientRef(ingredient_id=None, canonical_name="햄")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    req = request(vegan, probability_results=[probability(ham, ["is_pork"], 0.9)], locale="ko")
    llm = FakeLLM(RuntimeError("gateway down"))
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None


@pytest.mark.parametrize("locale", ["ko", "en", "ar", "zh-CN", "zh-TW", "ja", "es"])
def test_reason_tail_in_every_language(locale) -> None:
    from ai_decision_policy.messages import reason_tail

    text = reason_tail(["A", "B"], True, True, "80%", locale)
    assert "80%" in text and "None" not in text
    if locale != "ko":
        assert not any("가" <= ch <= "힣" for ch in text)


def test_reason_instructions_keep_reason_wording_out_of_the_review() -> None:
    # The high-risk review shares the Korean style; cards and how_often are the reason's.
    from ai_decision_policy.review import INSTRUCTIONS as REVIEW_INSTRUCTIONS
    from ai_decision_policy.reason import REASON_KOREAN_STYLE

    assert "주재료라서" not in instructions(REVIEW_INSTRUCTIONS, "ko")
    assert "주재료라서" in instructions(REASON_INSTRUCTIONS, "ko", korean_extra=REASON_KOREAN_STYLE)
    assert "menu name" in instructions(REASON_INSTRUCTIONS, "ko")
    assert '"this dish" in English' in instructions(REASON_INSTRUCTIONS, "en")


def test_reason_keeps_sauce_and_seasoning_as_plain_words() -> None:
    # 2026-11-22 잡채: "굴은 … 양념 안에 들어 있을 수 있어서" was dropped, since 양념 is
    # also an ingredient name the dish did not carry.
    oyster = IngredientRef(ingredient_id=None, canonical_name="굴")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    text = "굴은 조개류라 비건 기준에 맞지 않아요. 양념이나 소스 안에 들어 있어 빼기 어려울 수 있어요."
    llm = FakeLLM(ReasonOutput(reason=text))
    req = request(vegan, probability_results=[probability(oyster, ["is_shellfish"], 0.3, role="hidden")], locale="ko")
    response = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    assert response.message.reason.startswith(text)


def test_reason_payload_marks_names_that_already_show_the_cause() -> None:
    # 2026-11-22 칼국수: the reason listed 해물 and 새우젓 again as the verdict did.
    seafood = IngredientRef(ingredient_id=None, canonical_name="해물")
    llm = FakeLLM(ReasonOutput(reason="해물에는 새우가 들어갈 수 있어요."))
    req = request(
        SHRIMP_ALLERGY,
        probability_results=[probability(SHRIMP, ["is_shrimp"], 0.3), probability(seafood, ["is_shrimp"], 0.1)],
        locale="ko",
    )
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    names = [i["korean_name"] for g in llm.calls[0]["payload"]["explain_groups"] for i in g["ingredients"]]
    assert names == ["해물"]


def test_reason_groups_ingredients_by_what_is_avoided() -> None:
    # 2026-11-22 vegan 부대찌개: one sentence per group instead of one per ingredient.
    ham = IngredientRef(ingredient_id=None, canonical_name="햄")
    sausage = IngredientRef(ingredient_id=None, canonical_name="소시지")
    anchovy_sauce = IngredientRef(ingredient_id=None, canonical_name="멸치액젓")
    sand_lance = IngredientRef(ingredient_id=None, canonical_name="까나리")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    llm = FakeLLM(ReasonOutput(reason="햄과 소시지는 돼지고기로 만든 가공육이에요."))
    req = request(
        vegan,
        probability_results=[
            probability(ham, ["is_pork"], 0.95, role="identity"),
            probability(anchovy_sauce, ["is_fish"], 0.87, role="hidden"),
            probability(sausage, ["is_pork"], 0.71, role="identity"),
            probability(sand_lance, ["is_fish"], 0.3),
        ],
        locale="ko",
    )
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    groups = [(g["avoided_in_them"], [i["korean_name"] for i in g["ingredients"]])
              for g in llm.calls[0]["payload"]["explain_groups"]]
    assert groups == [(["돼지고기"], ["햄", "소시지"]), (["생선"], ["멸치액젓", "까나리"])]


@pytest.mark.parametrize(
    ("locale", "text"),
    [("ko", "굴소스 may contain 조개류예요."), ("zh-CN", "蚝油 may contain 贝类。"), ("ja", "オイスターソース may contain 貝類です。")],
)
def test_reason_check_drops_english_inside_another_language(locale, text) -> None:
    # 2026-11-17: an English example in the prompt was copied into Korean and Chinese.
    kwargs = dict(locale=locale, risk_level="caution", percents=[],
                  allowed_names=["굴소스", "조개류", "蚝油", "贝类", "オイスターソース", "貝類"])
    assert check_reason(text, **kwargs) is None


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


def missed(i: int, how_common: str = "many_recipes") -> MissedIngredient:
    return MissedIngredient(id=i, usual_use="새우젓으로 간을 함", how_common=how_common)


def test_review_without_llm_changes_nothing() -> None:
    req = review_request()
    response = asyncio.run(HighRiskReviewAgent(None).run(req))
    assert response.missed_ingredients == []
    assert response.owner_cards == req.decision.owner_cards
    assert response.reason == "이 메뉴의 재료 정보가 부족해요."


def test_review_asks_about_missed_ingredients_that_hit_the_user() -> None:
    llm = FakeLLM(ReviewOutput(
        missed_ingredients=[missed(130), missed(130), missed(999), missed(101)],
        reason="김치찌개에는 새우젓 때문에 새우가 들어갈 수 있어요.",
    ))
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


def test_review_drops_rare_additions() -> None:
    # Asked for ids alone, the LLM kept naming peanut for 김치찌개.
    llm = FakeLLM(ReviewOutput(
        missed_ingredients=[missed(150, "few_recipes"), missed(130, "most_recipes")],
        reason="새우가 들어갈 수 있어요.",
    ))
    req = review_request(user=SHRIMP_ALLERGY.model_copy(update={"allergies": ["is_shrimp", "is_peanut"]}))
    response = asyncio.run(HighRiskReviewAgent(llm).run(req))
    # Peanut can hit this user, so only how_common keeps it out.
    assert [c["id"] for c in llm.calls[0]["payload"]["ingredient_choices"]] == [130, 150]
    assert response.missed_ingredients == [SHRIMP]


def test_review_reason_may_not_name_a_choice_it_dropped() -> None:
    # 2026-11-12 제육볶음: the review dropped 땅콩 as rare, then wrote that it may be in
    # some versions. 땅콩 is also this user's restriction name, which the check allows.
    nut = SHRIMP_ALLERGY.model_copy(update={"allergies": ["is_shrimp", "is_peanut"]})
    llm = FakeLLM(ReviewOutput(
        missed_ingredients=[missed(150, "few_recipes"), missed(130, "most_recipes")],
        reason="새우가 들어갈 수 있고, 땅콩은 일부 변형에서 들어갈 수 있어요.",
    ))
    response = asyncio.run(HighRiskReviewAgent(llm).run(review_request(user=nut)))
    assert response.missed_ingredients == [SHRIMP]
    assert response.reason == "이 메뉴의 재료 정보가 부족해요."
    assert "Never mention a choice you did not list" in llm.calls[0]["instructions"]


def test_review_reason_may_name_a_kept_choice_and_the_restriction() -> None:
    nut = SHRIMP_ALLERGY.model_copy(update={"allergies": ["is_shrimp", "is_peanut"]})
    llm = FakeLLM(ReviewOutput(
        missed_ingredients=[missed(150, "many_recipes")],
        reason="땅콩을 피하신다면 땅콩이 들어갈 수 있어 확인이 필요해요.",
    ))
    response = asyncio.run(HighRiskReviewAgent(llm).run(review_request(user=nut)))
    assert response.missed_ingredients == [PEANUT]
    assert response.reason == "땅콩을 피하신다면 땅콩이 들어갈 수 있어 확인이 필요해요."


def test_review_gives_found_ingredients_in_the_users_language() -> None:
    llm = FakeLLM(ReviewOutput(missed_ingredients=[], reason="Tofu needs care here."))
    asyncio.run(HighRiskReviewAgent(llm).run(review_request(locale="en")))
    assert llm.calls[0]["payload"]["found_ingredients"][0]["localized_name"] == "tofu"
    assert "translate it into English" in llm.calls[0]["instructions"]


def test_review_keeps_the_old_reason_when_the_new_one_fails_the_check() -> None:
    llm = FakeLLM(ReviewOutput(missed_ingredients=[], reason="새우는 없어서 안전해요."))
    response = asyncio.run(HighRiskReviewAgent(llm).run(review_request()))
    assert response.reason == "이 메뉴의 재료 정보가 부족해요."


def test_review_falls_back_on_llm_error() -> None:
    req = review_request()
    response = asyncio.run(HighRiskReviewAgent(FakeLLM(RuntimeError("down"))).run(req))
    assert response.owner_cards == req.decision.owner_cards
    assert response.missed_ingredients == []


def test_more_known_limits_worth_a_reason() -> None:
    # 2026-11-02: a name-based guess, an estimate with no recipe data, a storeless scan.
    from ai_supervisor.contracts.decision import ProposedVariantIngredient

    from ai_decision_policy.reason import needs_reason, reason_facts

    variant = ProposedVariantIngredient(
        ingredient=PORK, constraint_tags=["is_pork"], role="identity", source="variant_suggested", evidence_refs=["v:1"]
    )
    guessed = request(HALAL, variants=[variant], locale="ko")
    unknown_prior = request(
        HALAL,
        probability_results=[probability(PORK, ["is_pork"], 0.3).model_copy(update={"prior_source": "uninformative"})],
        locale="ko",
    )
    storeless = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.62)], store_id=None, locale="ko")

    assert reason_facts(guessed) == ["돼지고기 is guessed from the menu name, not from recipes."]
    assert "no recipe data for 돼지고기" in reason_facts(unknown_prior)[0]
    assert "No restaurant was chosen" in " ".join(reason_facts(storeless))
    for req in (guessed, unknown_prior, storeless):
        assert needs_reason(req, judge(req))


def test_egg_named_gyeran_needs_no_explaining() -> None:
    from ai_decision_policy.reason import needs_explaining
    from types import SimpleNamespace

    def item(name, tags):
        return SimpleNamespace(ingredient=SimpleNamespace(canonical_name=name), matched_tags=tags)

    assert not needs_explaining(item("계란", ["is_egg"]))
    assert not needs_explaining(item("돼지갈비", ["is_pork"]))
    assert needs_explaining(item("배추김치", ["is_shrimp"]))


def test_partial_with_an_error_or_no_warning_is_still_a_load_failure() -> None:
    from ai_decision_policy.policy import information_failed
    from ai_supervisor.contracts.common import NodeError

    error = NodeError(code="x", node="ontology", item_id="scan-123:0", message="x", retryable=False, fallback=None)
    base = dict(probability_results=[probability(PORK, ["is_pork"], 0.6)], information_status="partial")
    assert information_failed(request(HALAL, **base))
    assert information_failed(request(HALAL, **base, warnings=[warning("insufficient_sources")]).model_copy(update={"errors": [error]}))
    assert not information_failed(request(HALAL, **base, warnings=[warning("unmapped_constraint_tags")]))


@pytest.mark.parametrize("text", [
    "This dish may include egg and peanut. The percentages show these ingredients can appear at nonzero rates.",
    "This dish may contain egg, and the ingredients are marked unknown.",
    "Wheat is listed as hidden, so the result is still estimated.",
])
def test_reason_may_not_talk_about_our_data(text) -> None:
    # 2026-11-18 scenario 6 (분식집, en): reasons described the data instead of the dish.
    assert check(text, locale="en", percents=(), names={"egg", "peanut", "wheat", "계란", "땅콩", "밀"}) is None


@pytest.mark.parametrize("text", [
    "Esta dish puede incluir huesos de res y carne de res.",
    "Es posible que this dish lleve carne de res.",
])
def test_spanish_reason_may_not_slip_into_english(text) -> None:
    # 2026-11-18 scenario 7 (힌두·es): "this dish" from the instructions was copied as is.
    assert check(text, locale="es", percents=(), names={"carne de res", "huesos de res", "소고기", "사골"}) is None
    ok = "Este plato puede llevar carne de res, así que conviene confirmarlo."
    assert check(ok, locale="es", percents=(), names={"carne de res", "소고기"}) == ok


def test_caution_reason_may_not_say_nothing_was_seen() -> None:
    # 2026-11-18 scenario 8 (페스코+우유, zh-CN): CAUTION for 갈비·된장·라면, yet the reason
    # said none of what the user avoids was seen.
    text = "另有调味用的成分需要确认，但没有看到你特别需要避开的牛、鸡、鸭、奶制品或猪肉成分。"
    assert check(text, locale="zh-CN", percents=(), names={"排骨", "갈비"}) is None


def test_reason_may_not_name_ingredient_roles_as_data() -> None:
    # 2026-11-18 scenario 9 (큰 메뉴판, en) and 7 (es).
    en = "This dish may contain nuts and peanut, which matter because they are listed as regular ingredients here."
    assert check(en, locale="en", percents=(), names={"nuts", "peanut", "견과류", "땅콩"}) is None
    es = "Es posible que este plato lleve carne de res, pero aparecen como inciertos."
    assert check(es, locale="es", percents=(), names={"carne de res", "소고기"}) is None


def test_no_reason_for_a_user_with_no_restriction() -> None:
    # 2026-11-18 scenario 10: 공기밥 got an LLM reason for a user who avoids nothing.
    from ai_decision_policy.tests.test_decision_policy import NO_RESTRICTION

    req = request(
        NO_RESTRICTION,
        probability_results=[probability(PORK, ["is_pork"], 0.5)],
        warnings=[warning("insufficient_sources")],
    )
    llm = FakeLLM(ReasonOutput(reason="이 음식은 확인된 자료가 적어요."))
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None
    assert llm.calls == []
