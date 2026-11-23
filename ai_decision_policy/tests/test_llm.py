"""⑧ LLM steps with a fake client: the reason sentence and its #174 checks (§0-2, §4),
and the high-risk review (§2-3). No network."""

import asyncio

import pytest

from ai_decision_policy import DecisionTool, HighRiskReviewAgent, ReasonWriter, XaiLLM, check_reason, judge
from ai_decision_policy.reason import MAX_REASON_CHARS, ReasonOutput, reason_instructions
from ai_decision_policy.tests.test_decision_policy import FISH_SAUCE
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


def one_phrase(text: str) -> ReasonOutput:
    """The LLM's answer for a menu with one group to explain."""

    return ReasonOutput(phrases=[text])


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 2026-11-22 halal 떡볶이: "가공육예요".
        ("돼지고기가 들어갈 수 있는 가공육예요.", "돼지고기가 들어갈 수 있는 가공육이에요."),
        ("돼지고기는 고기이에요.", "돼지고기는 고기예요."),
        ("돼지고기가 들어갈 수 있는 가공육이에요.", "돼지고기가 들어갈 수 있는 가공육이에요."),
    ],
)
def test_reason_check_fixes_korean_yeyo_after_a_final_consonant(text, expected) -> None:
    assert check(text) == expected


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
    assert asyncio.run(DecisionTool(ReasonWriter(FakeLLM(one_phrase(text)))).run(req)).message.reason is None
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
    # "made with fish" names the restriction, not an ingredient the request lacks.
    for locale, phrase, line in (
        ("ko", "생선을 발효시켜 만든 액젓", "• 액젓: 생선을 발효시켜 만든 액젓"),
        ("en", "a sauce fermented from fish", "• fish sauce: a sauce fermented from fish"),
    ):
        req = request(FISH_ALLERGY, probability_results=[probability(FISH_SAUCE, ["is_fish"], 0.8, role="hidden")],
                      localized_names={110: "fish sauce"} if locale == "en" else None, locale=locale)
        reason = asyncio.run(DecisionTool(ReasonWriter(FakeLLM(one_phrase(phrase)))).run(req)).message.reason
        # One line per group, then the card step (2026-11-23).
        assert reason.split("\n")[0] == line


def test_reason_may_translate_a_name_given_only_in_korean() -> None:
    # 까나리액젓 has no English name yet; the LLM translates it and says so (§4). The line
    # starts with the translation.
    sauce = IngredientRef(ingredient_id=None, canonical_name="까나리액젓")
    req = request(FISH_ALLERGY, probability_results=[probability(sauce, ["is_fish"], 0.5)])
    phrase = "a fish sauce made by fermenting small fish"
    told = ReasonOutput(name_translations=[{"korean": "까나리액젓", "translated": "sand lance fish sauce"}], phrases=[phrase])
    llm = FakeLLM(told)
    reason = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason
    assert reason.split("\n")[0] == f"• sand lance fish sauce: {phrase}"
    assert "name_translations" in llm.calls[0]["instructions"]
    # Without the translation the line would start in Korean, so it is dropped.
    silent = FakeLLM(one_phrase(phrase))
    assert asyncio.run(DecisionTool(ReasonWriter(silent)).run(req)).message.reason is None


def test_a_translation_only_counts_for_a_name_given_in_korean() -> None:
    # Pork already has an English name; "translating" it as shrimp adds shrimp.
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.62)], localized_names={PORK.ingredient_id: "pork"})
    told = ReasonOutput(name_translations=[{"korean": "돼지고기", "translated": "shrimp"}], phrases=["pork or shrimp"])
    assert asyncio.run(DecisionTool(ReasonWriter(FakeLLM(told))).run(req)).message.reason is None


def test_reason_check_trims_a_doubled_korean_ending() -> None:
    text = "추정 결과라서 단정할 수 없어요요. 레시피도 몇 개뿐이라 덜 확실해요요."
    assert check(text) == "추정 결과라서 단정할 수 없어요. 레시피도 몇 개뿐이라 덜 확실해요."


def prompt(locale: str, groups: list[dict] | None = None) -> str:
    return reason_instructions(request(HALAL, locale=locale), groups or [])


def test_korean_examples_go_only_to_korean() -> None:
    # An English example in the shared rules was copied into Korean and Chinese (2026-11-17).
    assert "만두: 돼지고기와 채소로" in prompt("ko")
    assert "만두: 돼지고기와 채소로" not in prompt("zh-TW")
    assert "Traditional Chinese" in prompt("zh-TW")
    # Phrases have no sentence ending, so no 해요체 rule (2026-11-23).
    assert "해요체" not in prompt("ko")


def test_reason_check_drops_empty_or_long_text() -> None:
    assert check("   ") is None
    assert check("돼지고기 " * (MAX_REASON_CHARS // 5 + 1)) is None


# --- reason sentence on the decision node ---------------------------------------


# 삼겹살 does not say "pork" by its name, so it is worth a reason (needs_reason).
PORK_BELLY = IngredientRef(ingredient_id=105, canonical_name="삼겹살")


def estimated_pork(locale="ko"):
    return request(HALAL, probability_results=[probability(PORK_BELLY, ["is_pork"], 0.62)], locale=locale)


# Card step, then the limit as the last line (2026-11-23).
LIMITED_PORK_REASON = "빼고 요청 카드로 빼 달라고 부탁할 수 있어요.\n이 메뉴는 웹에서 찾은 레시피가 적어서 결과가 덜 확실해요."


def test_no_reason_when_the_name_already_says_why() -> None:
    # "돼지고기 (약 62%)" for a halal user: a reason could only repeat the verdict.
    llm = FakeLLM(one_phrase("unused"))
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.62)], locale="ko")
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None
    assert llm.calls == []
    # The same menu with a known limit gets the fixed limit sentence, still without
    # the LLM (2026-11-23).
    limited = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.62)], locale="ko",
                      warnings=[warning("insufficient_sources")])
    reason = asyncio.run(DecisionTool(ReasonWriter(llm)).run(limited)).message.reason
    assert reason == LIMITED_PORK_REASON
    assert llm.calls == []


def test_decision_tool_adds_a_checked_reason_and_keeps_the_verdict() -> None:
    llm = FakeLLM(one_phrase("돼지의 뱃살 부위"))
    req = estimated_pork()
    response = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    plain = judge(req)
    assert response.message.reason == "• 삼겹살: 돼지의 뱃살 부위\n빼고 요청 카드로 빼 달라고 부탁할 수 있어요."
    assert response.model_dump(exclude={"message": {"reason"}}) == plain.model_dump(exclude={"message": {"reason"}})
    # Only the groups to explain go in: no percent, no full list, no verdict (2026-11-23).
    assert set(llm.calls[0]["payload"]) == {"menu", "explain_groups"}
    assert "Korean" in llm.calls[0]["instructions"]


@pytest.mark.parametrize(
    "llm",
    [
        FakeLLM(RuntimeError("gateway down")),
        FakeLLM(one_phrase("확률 약 80%의 돼지고기")),
        FakeLLM(one_phrase("늦은 답")),
        FakeLLM(ReasonOutput(phrases=["돼지의 뱃살 부위", "두 번째"])),
        FakeLLM(one_phrase("돼지의 뱃살 부위를 아주 길게 설명하는 문구라서 서른 자를 넘는 경우")),
    ],
)
def test_decision_tool_falls_back_to_no_reason(llm) -> None:
    # A failure, a percent not shown, too late, the wrong number of phrases, too long.
    if llm.output and getattr(llm.output, "phrases", []) == ["늦은 답"]:
        llm.delay = 0.2
    writer = ReasonWriter(llm, timeout_seconds=0.05)
    response = asyncio.run(DecisionTool(writer).run(estimated_pork()))
    assert response.message.reason is None
    assert response == judge(estimated_pork())


def test_decision_tool_skips_the_llm_when_nothing_drives_the_verdict() -> None:
    llm = FakeLLM(one_phrase("unused"))
    safe = request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, locale="ko")
    no_info = request(HALAL, no_information=True, locale="ko")
    for req in (safe, no_info):
        assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None
    assert llm.calls == []


def test_known_limits_are_fixed_sentences_without_the_llm() -> None:
    # 2026-11-23 새우튀김: with only 돼지고기 to show and a known limit, the LLM tied the
    # menu's other ingredients to pork. Limits are templates now and skip the LLM.
    llm = FakeLLM(one_phrase("unused"))
    req = request(
        HALAL,
        probability_results=[probability(PORK, ["is_pork"], 0.62)],
        warnings=[warning("insufficient_sources"), warning("insufficient_sources"), warning("some_internal_code")],
        information_status="partial",
        locale="ko",
    )
    reason = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason
    # partial here comes from the web warning, which says what happened itself; "could
    # not be loaded" would be untrue (2026-11-17). Duplicates and unknown codes drop.
    assert reason == LIMITED_PORK_REASON
    assert llm.calls == []


def test_groups_and_limits_send_only_the_groups_to_the_llm() -> None:
    llm = FakeLLM(one_phrase("돼지의 뱃살 부위"))
    req = request(
        HALAL,
        probability_results=[probability(PORK_BELLY, ["is_pork"], 0.62), probability(TOFU, [], 0.9)],
        store_id=None,
        locale="ko",
    )
    reason = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason
    assert "두부" not in str(llm.calls[0]["payload"])
    assert reason == (
        "• 삼겹살: 돼지의 뱃살 부위\n빼고 요청 카드로 빼 달라고 부탁할 수 있어요."
        "\n식당을 고르지 않아서 사장님 확인 없이 레시피로만 추정했어요."
    )


def test_only_the_most_serious_limit_is_shown() -> None:
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.62)], store_id=None,
                  warnings=[warning("insufficient_sources")], locale="ko")
    reason = asyncio.run(DecisionTool(ReasonWriter(FakeLLM(one_phrase("unused")))).run(req)).message.reason
    assert reason == LIMITED_PORK_REASON


def test_limits_still_go_when_the_llm_fails() -> None:
    req = request(HALAL, probability_results=[probability(PORK_BELLY, ["is_pork"], 0.62)],
                  warnings=[warning("insufficient_sources")], locale="ko")
    reason = asyncio.run(DecisionTool(ReasonWriter(FakeLLM(RuntimeError("down")))).run(req)).message.reason
    assert reason == LIMITED_PORK_REASON


def test_reason_may_name_only_ingredients_that_hit_the_user() -> None:
    # 2026-11-23 새우튀김: 간장·마요네즈 were in the request but hit nobody; naming them
    # as causes passed the old check.
    soy = IngredientRef(ingredient_id=None, canonical_name="간장")
    llm = FakeLLM(one_phrase("간장에 재운 돼지고기"))
    req = request(HALAL, probability_results=[probability(PORK_BELLY, ["is_pork"], 0.62), probability(soy, [], 0.9)],
                  locale="ko")
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None


def test_each_group_gets_its_own_line() -> None:
    fish_cake = IngredientRef(ingredient_id=201, canonical_name="어묵")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    req = request(vegan, probability_results=[probability(PORK_BELLY, ["is_pork"], 0.62),
                                              probability(fish_cake, ["is_fish"], 0.4)], locale="ko")
    llm = FakeLLM(ReasonOutput(phrases=["돼지의 뱃살 부위", "생선 살을 갈아 만든 가공식품"]))
    reason = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason
    assert reason.split("\n")[:2] == ["• 삼겹살: 돼지의 뱃살 부위", "• 어묵: 생선 살을 갈아 만든 가공식품"]
    # A phrase that fails the check drops its line only.
    one_bad = FakeLLM(ReasonOutput(phrases=["돼지의 뱃살 부위", "간장으로 만든 가공식품"]))
    reason = asyncio.run(DecisionTool(ReasonWriter(one_bad)).run(req)).message.reason
    assert reason.split("\n")[0] == "• 삼겹살: 돼지의 뱃살 부위" and "어묵" not in reason


def test_prompt_has_only_the_rules_the_menu_needs() -> None:
    korean_only = [{"avoided_in_them": ["pork"], "ingredients": [{"name": "햄", "korean_name": "햄"}]}]
    english = [{"avoided_in_them": ["pork"], "ingredients": [{"name": "ham", "korean_name": "햄"}]}]
    assert "name_translations" not in prompt("en", english)
    assert "name_translations" in prompt("en", korean_only)
    # The hard-to-remove sentence went on 2026-11-23 (the notes say it).
    assert "hard_to_remove" not in prompt("ko", english) and "빼기 어려울" not in prompt("ko", english)
    assert "At most 30" in prompt("ko") and "At most 70" in prompt("en")


def test_reason_payload_names_restrictions_in_the_users_language() -> None:
    kimchi = IngredientRef(ingredient_id=109, canonical_name="배추김치")
    llm = FakeLLM(one_phrase("Napa cabbage kimchi may contain shrimp."))
    req = request(SHRIMP_ALLERGY, probability_results=[probability(kimchi, ["is_shrimp"], 0.86)], locale="en")
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    assert llm.calls[0]["payload"]["explain_groups"] == [
        {"avoided_in_them": ["shrimp"], "ingredients": [
            {"name": "napa cabbage kimchi", "korean_name": "배추김치"}]}
    ]


def test_reason_payload_keeps_what_is_avoided_apart_from_the_users_restriction() -> None:
    # 2026-11-22: a vegan's 어묵 read "생선 제한" when the tag name was the only field.
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    fish_cake = IngredientRef(ingredient_id=201, canonical_name="어묵")
    llm = FakeLLM(one_phrase("어묵은 생선으로 만들어서 비건 기준에 맞지 않을 수 있어요."))
    req = request(vegan, probability_results=[probability(fish_cake, ["is_fish"], 0.46)], locale="ko")
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    group = llm.calls[0]["payload"]["explain_groups"][0]
    assert group["avoided_in_them"] == ["생선"]
    # The verdict names 비건; "…비건 기준에 맞지 않아요" in the reason only added words.
    assert "goes_against_user" not in group


def test_reason_payload_keeps_the_two_likeliest_groups() -> None:
    # 2026-11-23: at most two groups, likeliest first; no role, percent or how often.
    anchovy_sauce = IngredientRef(ingredient_id=None, canonical_name="멸치액젓")
    dumpling = IngredientRef(ingredient_id=None, canonical_name="만두")
    sauce = IngredientRef(ingredient_id=None, canonical_name="굴소스")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    llm = FakeLLM(ReasonOutput(phrases=["멸치를 발효시킨 액젓", "돼지고기 소를 넣어 빚은 음식"]))
    req = request(
        vegan,
        probability_results=[
            probability(anchovy_sauce, ["is_fish"], 0.96, role="hidden"),
            probability(dumpling, ["is_pork"], 0.6, role="component"),
            probability(sauce, ["is_shellfish"], 0.1, role="component"),
            probability(SHRIMP, ["is_shrimp"], 0.21, role="component"),
        ],
        locale="ko",
    )
    reason = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason
    groups = llm.calls[0]["payload"]["explain_groups"]
    assert [[i["korean_name"] for i in g["ingredients"]] for g in groups] == [["멸치액젓"], ["만두"]]
    assert all(set(i) == {"name", "korean_name"} for g in groups for i in g["ingredients"])
    # No "80% 이상" sentence any more (2026-11-23).
    assert reason == (
        "• 멸치액젓: 멸치를 발효시킨 액젓\n• 만두: 돼지고기 소를 넣어 빚은 음식"
        "\n빼고 요청 카드로 빼 달라고 부탁하거나, 질문 카드로 직원에게 직접 확인해 보세요."
    )


def test_reason_is_not_sent_without_a_checked_line() -> None:
    ham = IngredientRef(ingredient_id=None, canonical_name="햄")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    req = request(vegan, probability_results=[probability(ham, ["is_pork"], 0.9)], locale="ko")
    llm = FakeLLM(RuntimeError("gateway down"))
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None


@pytest.mark.parametrize("locale", ["ko", "en", "ar", "zh-CN", "zh-TW", "ja", "es"])
def test_card_step_in_every_language(locale) -> None:
    from ai_decision_policy.messages import card_step

    texts = [card_step(True, True, locale), card_step(True, False, locale), card_step(False, True, locale)]
    assert all(texts) and card_step(False, False, locale) == ""
    if locale != "ko":
        assert not any("가" <= ch <= "힣" for t in texts for ch in t)


def test_reason_prompt_stays_out_of_the_review() -> None:
    from ai_decision_policy.reason import instructions
    from ai_decision_policy.review import INSTRUCTIONS as REVIEW_INSTRUCTIONS

    assert "빚은 음식" not in instructions(REVIEW_INSTRUCTIONS, "ko")
    assert "해요체" in instructions(REVIEW_INSTRUCTIONS, "ko")


def test_reason_keeps_sauce_and_seasoning_as_plain_words() -> None:
    # 2026-11-22 잡채: "굴은 … 양념 안에 들어 있을 수 있어서" was dropped, since 양념 is
    # also an ingredient name the dish did not carry.
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    oyster_sauce = IngredientRef(ingredient_id=None, canonical_name="굴소스")
    llm = FakeLLM(one_phrase("굴을 졸여 만든 양념 소스"))
    req = request(vegan, probability_results=[probability(oyster_sauce, ["is_shellfish"], 0.3, role="hidden")], locale="ko")
    response = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    assert response.message.reason.startswith("• 굴소스: 굴을 졸여 만든 양념 소스")


def test_plain_ingredients_are_not_explained() -> None:
    # 2026-11-23: "까나리는 생선이라서 생선이 들어갈 수 있어요" said it again. 굴 is a
    # plain ingredient (taxonomy "base"), so a vegan's 굴 needs no line.
    from ai_decision_policy.reason import needs_reason

    oyster = IngredientRef(ingredient_id=None, canonical_name="굴")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    req = request(vegan, probability_results=[probability(oyster, ["is_shellfish"], 0.3)], locale="ko")
    assert not needs_reason(req, judge(req))


def test_reason_payload_marks_names_that_already_show_the_cause() -> None:
    # 2026-11-22 칼국수: the reason listed 해물 and 새우젓 again as the verdict did. 새우
    # names its cause; 해물 is a plain ingredient (2026-11-23); 배추김치 is explained.
    seafood = IngredientRef(ingredient_id=None, canonical_name="해물")
    kimchi = IngredientRef(ingredient_id=None, canonical_name="배추김치")
    llm = FakeLLM(one_phrase("젓갈을 넣어 담근 김치"))
    req = request(
        SHRIMP_ALLERGY,
        probability_results=[probability(SHRIMP, ["is_shrimp"], 0.3), probability(seafood, ["is_shrimp"], 0.1),
                             probability(kimchi, ["is_shrimp"], 0.5)],
        locale="ko",
    )
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    names = [i["korean_name"] for g in llm.calls[0]["payload"]["explain_groups"] for i in g["ingredients"]]
    assert names == ["배추김치"]


def test_reason_leaves_alcohol_named_alcohol_to_the_verdict() -> None:
    # 2026-11-22 halal: 알코올 (from 맛술) was explained as "may contain 술".
    alcohol = IngredientRef(ingredient_id=None, canonical_name="알코올")
    soju = IngredientRef(ingredient_id=None, canonical_name="소주")
    llm = FakeLLM(one_phrase("소주는 쌀 등을 발효해 만든 술이에요."))
    req = request(
        HALAL,
        probability_results=[probability(alcohol, ["is_alcohol"], 0.9, role="hidden"), probability(soju, ["is_alcohol"], 0.3)],
        locale="ko",
    )
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    # 소주 is a well-known alcohol too (2026-11-23), so nothing is left to explain.
    assert llm.calls == []


def test_reason_groups_ingredients_by_what_is_avoided() -> None:
    # 2026-11-22 vegan 부대찌개: one sentence per group instead of one per ingredient.
    dumpling = IngredientRef(ingredient_id=None, canonical_name="만두")
    sundae = IngredientRef(ingredient_id=None, canonical_name="순대")
    anchovy_sauce = IngredientRef(ingredient_id=None, canonical_name="멸치액젓")
    sand_lance = IngredientRef(ingredient_id=None, canonical_name="까나리")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    llm = FakeLLM(ReasonOutput(phrases=["돼지고기가 들어가는 음식", "생선을 발효시킨 액젓"]))
    req = request(
        vegan,
        probability_results=[
            probability(dumpling, ["is_pork"], 0.95, role="identity"),
            probability(anchovy_sauce, ["is_fish"], 0.87, role="hidden"),
            probability(sundae, ["is_pork"], 0.71, role="identity"),
            probability(sand_lance, ["is_fish"], 0.3),
        ],
        locale="ko",
    )
    asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    groups = [(g["avoided_in_them"], [i["korean_name"] for i in g["ingredients"]])
              for g in llm.calls[0]["payload"]["explain_groups"]]
    assert groups == [(["돼지고기"], ["만두", "순대"]), (["생선"], ["멸치액젓", "까나리"])]


def test_well_known_foods_are_not_explained() -> None:
    # 2026-11-23: "햄·소시지: 돼지고기로 만든 가공육" told nobody anything. Only the pair
    # counts: 햄 for pork is left out, 만두 for pork stays.
    from types import SimpleNamespace

    from ai_decision_policy.reason import needs_explaining

    def item(name, tag):
        return SimpleNamespace(ingredient=SimpleNamespace(canonical_name=name), matched_tags=[tag])

    for name, tag in [("햄", "is_pork"), ("소시지", "is_pork"), ("치즈", "is_milk"), ("두부", "is_soybean"),
                      ("라면", "is_wheat"), ("막걸리", "is_alcohol")]:
        assert not needs_explaining(item(name, tag))
    for name, tag in [("만두", "is_pork"), ("어묵", "is_fish"), ("맛살", "is_crab"), ("만두", "is_wheat")]:
        assert needs_explaining(item(name, tag))


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
    # can understand: a fixed sentence says it (2026-11-23, no LLM). No information at
    # all, or safe, still skips.
    llm = FakeLLM(one_phrase("unused"))
    uncertain = request(HALAL, probability_results=[probability(TOFU, [], 0.8)], information_status="partial", locale="ko")
    reason = asyncio.run(DecisionTool(ReasonWriter(llm)).run(uncertain)).message.reason
    assert reason == "재료 정보 일부를 불러오지 못했어요."
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

    from ai_decision_policy.reason import limit_text, needs_reason

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

    assert limit_text(guessed) == ["돼지고기는 레시피가 아니라 메뉴 이름으로 추정한 재료예요."]
    assert limit_text(unknown_prior) == ["돼지고기는 이 메뉴의 레시피 데이터가 없어서 대략적인 기본값으로 추정했어요."]
    assert limit_text(storeless) == ["식당을 고르지 않아서 사장님 확인 없이 레시피로만 추정했어요."]
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
    llm = FakeLLM(one_phrase("이 음식은 확인된 자료가 적어요."))
    assert asyncio.run(DecisionTool(ReasonWriter(llm)).run(req)).message.reason is None
    assert llm.calls == []


def test_shrimp_tempura_with_only_pork_gets_no_llm_story() -> None:
    # 2026-11-23 새우튀김, storeless scan: only 돼지고기 (4.5%) hits the user. The reason
    # said 가쓰오부시, 간장, 튀김가루 … could carry pork. Now: the limit and the card only.
    others = [IngredientRef(ingredient_id=None, canonical_name=n) for n in ("가쓰오부시", "간장", "튀김가루")]
    req = request(
        HALAL,
        probability_results=[probability(PORK, ["is_pork"], 0.045), *(probability(o, [], 0.5) for o in others)],
        store_id=None,
        locale="ko",
    )
    llm = FakeLLM(one_phrase("unused"))
    response = asyncio.run(DecisionTool(ReasonWriter(llm)).run(req))
    assert llm.calls == []
    assert response.message.reason == (
        "질문 카드로 직원에게 직접 확인해 보세요.\n식당을 고르지 않아서 사장님 확인 없이 레시피로만 추정했어요."
    )
    assert response.message.verdict.endswith("돼지고기가 들어갈 확률이 4% 이상이에요.")


@pytest.mark.parametrize("locale", ["ko", "en", "ar", "zh-CN", "zh-TW", "ja", "es"])
def test_limit_sentences_in_every_language(locale) -> None:
    from ai_decision_policy.messages import LIMIT_SENTENCES, limit_sentences

    parts = limit_sentences(list(LIMIT_SENTENCES), ["A"], ["B"], locale)
    assert len(parts) == len(LIMIT_SENTENCES) + 2
    # Most serious first: no information leads, a storeless scan comes last.
    assert parts[0] == LIMIT_SENTENCES["no_information"][locale]
    assert parts[-1] == LIMIT_SENTENCES["no_store"][locale]
    assert any("A" in p for p in parts) and any("B" in p for p in parts)
    if locale != "ko":
        assert not any("가" <= ch <= "힣" for p in parts for ch in p)


def test_reason_may_name_what_a_flagged_ingredient_is_made_from() -> None:
    # First real run (2026-11-23, vegan 부대찌개): "멸치와 까나리로 만든" was dropped for
    # 멸치, a name inside 멸치액젓.
    anchovy_sauce = IngredientRef(ingredient_id=None, canonical_name="멸치액젓")
    vegan = profile(is_vegetarian=True, vegetarian_type="vegan")
    phrase = "멸치를 소금에 절여 발효시킨 액젓"
    req = request(vegan, probability_results=[probability(anchovy_sauce, ["is_fish"], 0.6)], locale="ko")
    reason = asyncio.run(DecisionTool(ReasonWriter(FakeLLM(one_phrase(phrase)))).run(req)).message.reason
    assert reason.startswith(f"• 멸치액젓: {phrase}")
