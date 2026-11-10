"""Result message text from docs/8-agent-xai.md §4, §4-1 and the §7 cases that check it
(1, 2, 3, 11~14, 16, 18, 21, 31). Verdict lines follow the front result screen (2026-11-06)."""

import pytest

from ai_decision_policy import DecisionTool, build_message, decide, judge
from ai_decision_policy import messages
from ai_decision_policy.tests.test_decision_policy import (
    FISH_ALLERGY,
    HALAL,
    KIMCHI,
    KIMCHI_STEW_CONFIRMED,
    NO_RESTRICTION,
    NO_SPICY,
    PORK,
    RICE,
    SHRIMP_ALLERGY,
    TUNA,
    confirmed,
    flag_answer,
    probability,
    profile,
    request,
)
from ai_supervisor.contracts.common import IngredientRef
from ai_supervisor.contracts.decision import DecisionTagObservation

LOCALES = ["ko", "en", "ar", "zh-CN", "zh-TW", "ja", "es"]
SHRIMP = IngredientRef(ingredient_id=130, canonical_name="새우")
ONION = IngredientRef(ingredient_id=140, canonical_name="양파")
SEAFOOD_ALLERGY = profile(allergies=["is_fish", "is_shrimp"])


def message(req):
    return build_message(req, decide(req))


# The verdict line follows the result screen (front Figma S10, 2026-11-06): who confirmed
# what, what is still unchecked, which restriction it breaks. No percentages.
VEGAN = profile(is_vegetarian=True, vegetarian_type="vegan")
EGG_GARNISH = IngredientRef(ingredient_id=200, canonical_name="달걀 지단")
FISH_CAKE = IngredientRef(ingredient_id=201, canonical_name="어묵")


def test_1_confirmed_danger_names_the_ingredient_and_the_restriction() -> None:
    results = [confirmed(PORK, ["is_pork"], "present")]
    assert message(request(HALAL, confirmed_results=results, locale="ko")).verdict == (
        "사장님이 돼지고기가 들어 있다고 확인했어요. 할랄 기준에 맞지 않아요."
    )
    en = request(HALAL, confirmed_results=results, localized_names={101: "pork"})
    assert message(en).verdict == (
        "The owner confirmed this dish contains pork. It doesn't fit your dietary needs (halal)."
    )


def test_danger_names_only_the_restriction_it_breaks() -> None:
    user = profile(is_vegetarian=True, vegetarian_type="vegan", allergies=["is_shrimp"])
    req = request(user, confirmed_results=[confirmed(FISH_CAKE, ["is_fish"], "present")], locale="ko")
    assert message(req).verdict == "사장님이 어묵이 들어 있다고 확인했어요. 비건 기준에 맞지 않아요."
    shrimp = request(user, confirmed_results=[confirmed(SHRIMP, ["is_shrimp"], "present")], locale="ko")
    assert message(shrimp).verdict.endswith("비건 · 새우 알레르기 기준에 맞지 않아요.")


def test_2_and_11_estimate_asks_to_check_without_percent() -> None:
    results = [probability(PORK, ["is_pork"], 0.62)]
    ko = request(HALAL, probability_results=results, locale="ko")
    assert message(ko).verdict == "돼지고기는 아직 확인이 필요해요."
    en = request(HALAL, probability_results=results, localized_names={101: "pork"})
    assert message(en).verdict == "We still need to check for pork."


def test_caution_says_what_the_owner_ruled_out_first() -> None:
    # S10-01: "사장님이 달걀 지단은 넣지 않는다고 확인했어요. 어묵은 아직 확인이 필요해요."
    req = request(
        VEGAN,
        confirmed_results=[confirmed(EGG_GARNISH, ["is_egg"], "absent")],
        probability_results=[probability(FISH_CAKE, ["is_fish"], 0.46)],
        locale="ko",
    )
    assert message(req).verdict == "사장님이 달걀 지단은 넣지 않는다고 확인했어요. 어묵은 아직 확인이 필요해요."
    zh = req.model_copy(update={"locale": "zh-CN"})
    # Chinese sentences join without a space (the untranslated name keeps its own).
    assert " " not in message(zh).verdict.replace("달걀 지단", "")


def test_3_no_information_asks_the_staff() -> None:
    req = request(HALAL, no_information=True, locale="ko")
    assert message(req).verdict == "재료 정보를 찾지 못했어요. 직원에게 직접 확인해 주세요."


def test_12_owner_confirmed_safe_names_the_restriction() -> None:
    req = request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, locale="ko")
    assert decide(req).risk_level == "safe"
    assert message(req).verdict == "사장님이 확인한 재료에 할랄 기준에 맞지 않는 재료가 없어요."


def test_safe_without_any_restriction_has_nothing_to_name() -> None:
    req = request(NO_RESTRICTION, confirmed_results=[confirmed(RICE, [], "present")], locale="ko")
    assert message(req).verdict == "사장님이 확인한 재료 중 피해야 할 재료가 없어요."


def test_estimate_without_overlap_does_not_sound_safe() -> None:
    req = request(HALAL, probability_results=[probability(ONION, [], 0.9)], locale="ko")
    assert message(req).verdict == "피해야 할 재료는 찾지 못했지만, 추정 결과라 확인이 필요해요."


def test_13_identity_note_text() -> None:
    req = request(
        SEAFOOD_ALLERGY,
        confirmed_results=[confirmed(TUNA, ["is_fish"], role="identity"), confirmed(RICE, [], "present")],
        locale="ko",
    )
    result = message(req)
    assert result.verdict == "사장님이 참치가 들어 있다고 확인했어요. 생선 알레르기 기준에 맞지 않아요."
    assert [(n.code, n.text) for n in result.notes] == [
        ("hard_to_remove", "참치는 이 메뉴의 주재료라 빼기 어려울 수 있어요.")
    ]


def test_14_hidden_note_text() -> None:
    req = request(
        SHRIMP_ALLERGY,
        confirmed_results=[confirmed(KIMCHI, [], "present")],
        probability_results=[probability(SHRIMP, ["is_shrimp"], 0.4, role="hidden")],
        localized_names={130: "shrimp"},
    )
    assert [(n.code, n.text) for n in message(req).notes] == [
        (
            "hard_to_remove",
            "Shrimp is inside another ingredient such as a sauce or seasoning, so it may be hard to leave out.",
        )
    ]


def test_16_spicy_observation_is_named() -> None:
    observation = DecisionTagObservation(tag="is_spicy", confidence=0.78, evidence_refs=["ocr:is_spicy"])
    req = request(NO_SPICY, confirmed_results=KIMCHI_STEW_CONFIRMED, observations=[observation], locale="ko")
    assert message(req).verdict == "매운 재료는 아직 확인이 필요해요."


def test_18_unresolved_flag_note() -> None:
    req = request(FISH_ALLERGY, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_unclear_jeotgal"], locale="ko")
    result = message(req)
    assert result.verdict == "아직 확인되지 않은 정보가 있어요. 직원에게 직접 확인해 주세요."
    assert [(n.code, n.text) for n in result.notes] == [
        ("ambiguous_menu", "젓갈에 생선이 들어갈 수 있어 확인이 필요해요.")
    ]


def test_variant_flag_note_lists_every_forbidden_tag() -> None:
    req = request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_variant"], locale="ko")
    assert [n.text for n in message(req).notes] == [
        "재료가 바뀐 메뉴라 술이나 돼지고기가 들어갈 수 있어 확인이 필요해요."
    ]


def test_21_flag_answer_danger_names_the_tag() -> None:
    req = request(
        FISH_ALLERGY,
        confirmed_results=KIMCHI_STEW_CONFIRMED,
        flags=["has_unclear_jeotgal"],
        flag_answers=[flag_answer(["is_fish"], "present")],
        locale="ko",
    )
    assert message(req).verdict == "사장님이 생선이 들어 있다고 확인했어요. 생선 알레르기 기준에 맞지 않아요."


def test_missing_localized_name_falls_back_to_the_team_tables() -> None:
    # No localized_names from the backend: the standard table still names 돼지고기 (names.py).
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.3)], locale="ja")
    assert message(req).verdict == "豚肉はまだ確認が必要です。"


def test_unmapped_web_ingredient_is_named_from_the_extra_table() -> None:
    # ④ could not map 삼치 (ingredient_id null); the extra table has it (2026-11-02).
    mackerel = IngredientRef(ingredient_id=None, canonical_name="삼치")
    req = request(SEAFOOD_ALLERGY, probability_results=[probability(mackerel, ["is_fish"], 0.4)], locale="en")
    assert message(req).verdict == "We still need to check for Spanish mackerel."


def test_name_in_no_table_keeps_korean() -> None:
    sauce = IngredientRef(ingredient_id=None, canonical_name="까나리액젓")
    req = request(SEAFOOD_ALLERGY, probability_results=[probability(sauce, ["is_fish"], 0.3)], locale="ja")
    assert message(req).verdict == "까나리액젓はまだ確認が必要です。"


def test_same_ingredient_twice_is_named_once_with_higher_probability() -> None:
    # ④ can send one ingredient per tag bundle (#327).
    req = request(
        SEAFOOD_ALLERGY,
        probability_results=[probability(SHRIMP, ["is_shrimp"], 0.2), probability(SHRIMP, ["is_fish"], 0.7)],
        locale="ko",
    )
    assert message(req).verdict == "새우는 아직 확인이 필요해요."


def test_several_ingredients_highest_probability_first() -> None:
    req = request(
        SEAFOOD_ALLERGY,
        probability_results=[probability(SHRIMP, ["is_shrimp"], 0.2), probability(TUNA, ["is_fish"], 0.7)],
        localized_names={130: "shrimp", 103: "tuna"},
    )
    assert message(req).verdict == "We still need to check for tuna and shrimp."


NINE_FISH = [
    IngredientRef(ingredient_id=300 + i, canonical_name=name)
    for i, name in enumerate(["멸치", "새우젓", "까나리", "참치", "꽁치", "굴", "어묵", "다시마", "생선"])
]


def _nine_fish(locale: str):
    # Probability falls with the index, so the order stays the list order.
    return request(
        SEAFOOD_ALLERGY,
        probability_results=[
            probability(ref, ["is_fish"], 0.9 - i * 0.05) for i, ref in enumerate(NINE_FISH)
        ],
        locale=locale,
    )


def test_verdict_names_at_most_seven_ingredients_and_counts_the_rest() -> None:
    # 2026-11-08: the screen lists every ingredient with its probability under the
    # sentence (Figma S10), so the sentence stops at seven, highest probability first.
    assert message(_nine_fish("ko")).verdict == (
        "멸치, 새우젓, 까나리, 참치, 꽁치, 굴, 어묵 외 2개는 아직 확인이 필요해요."
    )
    en = message(_nine_fish("en")).verdict
    assert en.startswith("We still need to check for ") and en.endswith(" and 2 more.")
    assert en.count(", ") == 6
    assert message(_nine_fish("ja")).verdict.endswith("ほか2種類はまだ確認が必要です。")
    assert message(_nine_fish("zh-CN")).verdict.endswith("及其他2种还需要确认。")
    assert message(_nine_fish("zh-TW")).verdict.endswith("及其他2種還需要確認。")
    assert message(_nine_fish("es")).verdict.endswith(" y 2 más.")
    assert "من المكونات الأخرى" in message(_nine_fish("ar")).verdict


def test_seven_ingredients_are_all_named() -> None:
    req = request(
        SEAFOOD_ALLERGY,
        probability_results=[probability(ref, ["is_fish"], 0.9 - i * 0.05) for i, ref in enumerate(NINE_FISH[:7])],
        locale="ko",
    )
    assert message(req).verdict == "멸치, 새우젓, 까나리, 참치, 꽁치, 굴, 어묵은 아직 확인이 필요해요."


def test_31_reason_is_empty_but_verdict_and_notes_stay() -> None:
    req = request(SEAFOOD_ALLERGY, confirmed_results=[confirmed(TUNA, ["is_fish"], role="identity")])
    result = message(req)
    assert result.reason is None
    assert result.verdict and [n.code for n in result.notes] == ["hard_to_remove"]


TEMPLATES = [
    messages.AND_LISTS,
    messages.OWNER_SAYS_PRESENT,
    messages.BREAKS,
    messages.OWNER_SAYS_ABSENT,
    messages.STILL_TO_CHECK,
    messages.SAFE_FOR,
    messages.SAFE,
    messages.NOT_CONFIRMED,
    messages.NO_INFORMATION,
    messages.NOT_ENOUGH_INFORMATION,
    messages.ALLERGY,
    messages.RESTRICTION_SEPARATOR,
    *messages.RESTRICTION_NAMES.values(),
    messages.AMBIGUOUS_PLACE,
    messages.AMBIGUOUS_VARIANT,
    messages.MAIN_INGREDIENT,
    messages.HIDDEN_INGREDIENT,
]


@pytest.mark.parametrize("table", TEMPLATES)
def test_every_template_has_all_seven_languages(table) -> None:
    assert sorted(table) == sorted(LOCALES)


@pytest.mark.parametrize(
    "table", [messages.SAFE, messages.NOT_CONFIRMED, messages.NO_INFORMATION, messages.NOT_ENOUGH_INFORMATION]
)
def test_chinese_scripts_differ(table) -> None:
    assert table["zh-CN"] != table["zh-TW"]


@pytest.mark.parametrize("lang", LOCALES)
def test_every_language_builds_every_sentence(lang) -> None:
    cases = [
        request(SEAFOOD_ALLERGY, confirmed_results=[confirmed(TUNA, ["is_fish"], role="identity")], locale=lang),
        request(SHRIMP_ALLERGY, probability_results=[probability(SHRIMP, ["is_shrimp"], 0.4, role="hidden")], locale=lang),
        request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, locale=lang),
        request(HALAL, no_information=True, locale=lang),
        request(NO_RESTRICTION, probability_results=[probability(ONION, [], 0.5)], locale=lang),
        request(FISH_ALLERGY, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_unclear_broth", "has_unclear_jeotgal", "has_hidden_animal", "has_variant"], locale=lang),
    ]
    for req in cases:
        result = message(req)
        assert result.verdict
        assert all(n.text for n in result.notes)
    # The same sentence is never shown in two different languages by mistake.
    if lang != "ko":
        assert message(cases[2]).verdict != message(cases[2].model_copy(update={"locale": "ko"})).verdict


# Adapter


def test_judge_wraps_verdict_message_and_cards() -> None:
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.8)], localized_names={101: "pork"})
    response = judge(req)
    assert (response.risk_level, response.evidence_basis) == ("caution", "estimated")
    assert response.message.verdict == "We still need to check for pork."
    assert [card.ingredient.canonical_name for card in response.owner_cards] == ["돼지고기"]
    assert response.risk_ingredients[0].ingredient.localized_name == "pork"


def test_33_storeless_scan_has_no_cards_and_is_not_safe() -> None:
    import asyncio

    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.8)], store_id=None)
    response = asyncio.run(DecisionTool().run(req))
    assert response.risk_level == "caution"
    assert response.owner_cards == []
    assert not response.high_risk_review.required
    assert [w.code for w in response.warnings] == ["store_context_missing"]
