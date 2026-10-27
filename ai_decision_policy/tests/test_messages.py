"""Result message text from docs/8-agent-xai.md §4, §4-1 and the §7 cases that check it
(1, 2, 3, 11~14, 16, 18, 21, 31)."""

import pytest

from ai_decision_policy import DecisionTool, build_message, decide, judge
from ai_decision_policy import messages
from ai_decision_policy.messages import percent
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


def test_1_confirmed_danger_names_the_ingredient() -> None:
    results = [confirmed(PORK, ["is_pork"], "present")]
    assert message(request(HALAL, confirmed_results=results, locale="ko")).verdict == "돼지고기가 들어 있어요."
    en = request(HALAL, confirmed_results=results, localized_names={101: "pork"})
    assert message(en).verdict == "This dish contains pork."


def test_2_and_11_estimate_shows_whole_percent() -> None:
    results = [probability(PORK, ["is_pork"], 0.62)]
    ko = request(HALAL, probability_results=results, locale="ko")
    assert message(ko).verdict == "돼지고기(약 62%)가 들어 있을 수 있어요."
    en = request(HALAL, probability_results=results, localized_names={101: "pork"})
    assert message(en).verdict == "This dish may contain pork (about 62%)."


@pytest.mark.parametrize(
    ("posterior_mean", "ko"),
    [(0.004, "1% 미만"), (0.01, "약 1%"), (0.625, "약 63%"), (0.99, "약 99%"), (0.995, "99% 이상")],
)
def test_percent_never_reads_zero_or_hundred(posterior_mean, ko) -> None:
    assert percent(posterior_mean, "ko") == ko
    for lang in LOCALES:
        for p in (0.0, 0.001, 0.999, 1.0):
            text = percent(p, lang)
            assert "100" not in text and not text.lstrip("约約").startswith("0")


def test_3_no_information_says_so() -> None:
    req = request(HALAL, no_information=True, locale="ko")
    assert message(req).verdict == "정보가 부족해 안전한지 확인할 수 없어요."


def test_12_owner_confirmed_safe() -> None:
    req = request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, locale="ko")
    assert decide(req).risk_level == "safe"
    assert message(req).verdict == "사장님이 확인한 재료 중 피해야 할 재료가 없어요."


def test_estimate_without_overlap_does_not_sound_safe() -> None:
    req = request(HALAL, probability_results=[probability(ONION, [], 0.9)], locale="ko")
    assert message(req).verdict == "피해야 할 재료는 찾지 못했지만, 추정 결과라 안전하다고 단정할 수 없어요."


def test_13_identity_note_text() -> None:
    req = request(
        SEAFOOD_ALLERGY,
        confirmed_results=[confirmed(TUNA, ["is_fish"], role="identity"), confirmed(RICE, [], "present")],
        locale="ko",
    )
    result = message(req)
    assert result.verdict == "참치가 들어 있어요."
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
    assert message(req).verdict == "매운 재료가 들어 있을 수 있어요."


def test_18_unresolved_flag_note() -> None:
    req = request(FISH_ALLERGY, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_unclear_jeotgal"], locale="ko")
    result = message(req)
    assert result.verdict == "정보가 부족해 안전한지 확인할 수 없어요."
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
    assert message(req).verdict == "생선이 들어 있어요."


def test_missing_translation_keeps_korean_name() -> None:
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.3)], locale="ja")
    assert message(req).verdict == "この料理には돼지고기（約30%）が入っている可能性があります。"


def test_same_ingredient_twice_is_named_once_with_higher_probability() -> None:
    # ④ can send one ingredient per tag bundle (#327).
    req = request(
        SEAFOOD_ALLERGY,
        probability_results=[probability(SHRIMP, ["is_shrimp"], 0.2), probability(SHRIMP, ["is_fish"], 0.7)],
        locale="ko",
    )
    assert message(req).verdict == "새우(약 70%)가 들어 있을 수 있어요."


def test_several_ingredients_highest_probability_first() -> None:
    req = request(
        SEAFOOD_ALLERGY,
        probability_results=[probability(SHRIMP, ["is_shrimp"], 0.2), probability(TUNA, ["is_fish"], 0.7)],
        localized_names={130: "shrimp", 103: "tuna"},
    )
    assert message(req).verdict == "This dish may contain tuna (about 70%) and shrimp (about 20%)."


def test_31_reason_is_empty_but_verdict_and_notes_stay() -> None:
    req = request(SEAFOOD_ALLERGY, confirmed_results=[confirmed(TUNA, ["is_fish"], role="identity")])
    result = message(req)
    assert result.reason is None
    assert result.verdict and [n.code for n in result.notes] == ["hard_to_remove"]


TEMPLATES = [
    messages.PERCENT,
    messages.WITH_PERCENT,
    messages.AND_LISTS,
    messages.CONTAINS,
    messages.MAY_CONTAIN,
    messages.SAFE,
    messages.NOT_CONFIRMED,
    messages.NOT_ENOUGH_INFORMATION,
    messages.AMBIGUOUS_PLACE,
    messages.AMBIGUOUS_VARIANT,
    messages.MAIN_INGREDIENT,
    messages.HIDDEN_INGREDIENT,
]


@pytest.mark.parametrize("table", TEMPLATES)
def test_every_template_has_all_seven_languages(table) -> None:
    assert sorted(table) == sorted(LOCALES)


@pytest.mark.parametrize("table", [messages.SAFE, messages.NOT_CONFIRMED, messages.NOT_ENOUGH_INFORMATION])
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
        assert message(cases[2]).verdict != messages.SAFE["ko"]


# Adapter


def test_judge_wraps_verdict_message_and_cards() -> None:
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.8)], localized_names={101: "pork"})
    response = judge(req)
    assert (response.risk_level, response.evidence_basis) == ("caution", "estimated")
    assert response.message.verdict == "This dish may contain pork (about 80%)."
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
