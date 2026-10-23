"""Owner question cards from docs/8-agent-xai.md §7 (5, 17, 18~24, 26~30, 33, 34)."""

from typing import get_args

import pytest

from ai_decision_policy import build_owner_cards, decide
from ai_decision_policy.questions import (
    DISH_QUESTIONS,
    FLAG_PLACES,
    LISTS,
    PLACE_QUESTIONS,
    TAG_NAMES,
)
from ai_decision_policy.tests.test_decision_policy import (
    BEEF,
    FISH_ALLERGY,
    FISH_SAUCE,
    HALAL,
    KIMCHI_STEW_CONFIRMED,
    MILK,
    NO_RESTRICTION,
    PORK,
    SHRIMP_ALLERGY,
    VEGAN,
    confirmed,
    flag_answer,
    probability,
    profile,
    request,
)
from ai_supervisor.contracts.common import IngredientRef, Locale
from ai_supervisor.contracts.decision import ConfirmedResult, ProposedVariantIngredient

SHRIMP = IngredientRef(ingredient_id=130, canonical_name="새우")
CHICKEN = IngredientRef(ingredient_id=140, canonical_name="닭고기")


def cards(req):
    return build_owner_cards(req, decide(req))


def expired(ingredient: IngredientRef, tags: list[str], status: str = "absent") -> ConfirmedResult:
    return confirmed(ingredient, tags, status).model_copy(
        update={"expired": True, "override_eligible": False}
    )


def state(ingredient: IngredientRef | None, confirmation_state: str, round_: int, **extra) -> dict:
    return {
        "question_id": extra.get("question_id", "question-002"),
        "ingredient": ingredient.model_dump() if ingredient else None,
        "ambiguity_flag": extra.get("flag"),
        "constraint_tags": extra.get("tags", []),
        "answered_round": round_,
        "confirmation_state": confirmation_state,
    }


def test_05_unrelated_ingredients_get_no_card() -> None:
    req = request(HALAL, probability_results=[probability(MILK, ["is_milk"], 0.4)])
    assert cards(req) == []


def test_no_restriction_gets_no_card() -> None:
    req = request(NO_RESTRICTION, probability_results=[probability(PORK, ["is_pork"], 0.8)])
    assert cards(req) == []


def test_unconfirmed_overlap_gets_one_card_per_ingredient() -> None:
    req = request(
        VEGAN,
        probability_results=[probability(MILK, ["is_milk"], 0.3), probability(BEEF, ["is_beef"], 0.9)],
    )
    result = cards(req)
    assert [c.ingredient for c in result] == [BEEF, MILK]  # higher probability first
    card = result[0]
    assert card.card_id == "card:scan-123:0:ingredient-120:r1"
    assert (card.round, card.previous_question_id) == (1, None)
    assert (card.ambiguity_flag, card.constraint_tags) == (None, [])
    assert card.menu_id == 1001
    assert card.evidence_refs == ["recipe:120"]


def test_probability_number_does_not_filter_cards() -> None:
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.01)])
    assert [c.ingredient for c in cards(req)] == [PORK]


def test_confident_unconfirmed_ingredient_is_unconfirmed() -> None:
    p = probability(PORK, ["is_pork"], 0.6).model_copy(update={"confidence": 0.9})
    assert cards(request(HALAL, probability_results=[p]))[0].reason_codes == ["unconfirmed"]


def test_low_confidence_is_insufficient_recipe_evidence() -> None:
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.6)])  # confidence 0.55
    assert cards(req)[0].reason_codes == ["insufficient_recipe_evidence"]


def test_eligible_owner_answer_gets_no_card() -> None:
    req = request(
        HALAL,
        confirmed_results=[confirmed(PORK, ["is_pork"], "absent")],
        probability_results=[probability(PORK, ["is_pork"], 0.8)],
    )
    assert cards(req) == []


def test_unknown_owner_answer_still_gets_a_card() -> None:
    req = request(HALAL, confirmed_results=[confirmed(PORK, ["is_pork"], "unknown")])
    assert [(c.ingredient, c.reason_codes) for c in cards(req)] == [(PORK, ["unconfirmed"])]


def test_variant_guess_gets_a_card() -> None:
    variant = ProposedVariantIngredient(
        ingredient=BEEF,
        constraint_tags=["is_beef"],
        role=None,
        source="variant_suggested",
        evidence_refs=["ocr:variant"],
    )
    req = request(VEGAN, variants=[variant])
    assert [(c.ingredient, c.reason_codes) for c in cards(req)] == [(BEEF, ["unconfirmed"])]


def test_ingredient_without_tags_gets_no_card() -> None:
    req = request(HALAL, probability_results=[probability(PORK, [], 0.8)])
    assert cards(req) == []


def test_same_ingredient_is_one_card() -> None:
    req = request(
        HALAL,
        confirmed_results=[expired(PORK, ["is_pork"])],
        probability_results=[probability(PORK, ["is_pork"], 0.8)],
    )
    result = cards(req)
    assert len(result) == 1
    assert result[0].reason_codes == ["expired", "insufficient_recipe_evidence"]
    assert result[0].evidence_refs == ["evidence-101", "recipe:101"]


def test_17_26_expired_answer_is_asked_again_without_anomaly() -> None:
    req = request(
        HALAL,
        confirmed_results=[expired(PORK, ["is_pork"])],
        probability_results=[
            probability(PORK, ["is_pork"], 0.8).model_copy(update={"confidence": 0.9})
        ],
    )
    assert cards(req)[0].reason_codes == ["expired"]


def test_24_anomaly_reason_comes_first() -> None:
    req = request(
        HALAL,
        probability_results=[
            probability(MILK, ["is_pork"], 0.99),
            probability(PORK, ["is_pork"], 0.95, anomaly_locked=True),
        ],
    )
    result = cards(req)
    assert result[0].ingredient == PORK
    assert result[0].reason_codes[0] == "anomaly"


def test_cards_are_ordered_by_reason_then_probability() -> None:
    req = request(
        profile(religion_type="halal", allergies=["is_fish"]),
        confirmed_results=[expired(BEEF, ["is_pork"])],
        probability_results=[
            probability(SHRIMP, ["is_pork"], 0.2),
            probability(MILK, ["is_pork"], 0.7),
            probability(PORK, ["is_pork"], 0.1, anomaly_locked=True),
        ],
        flags=["has_unclear_jeotgal"],
    )
    result = cards(req)
    assert [c.reason_codes[0] for c in result] == [
        "anomaly",
        "expired",
        "ambiguous_menu",
        "insufficient_recipe_evidence",
        "insufficient_recipe_evidence",
    ]
    assert [c.ingredient for c in result[3:]] == [MILK, SHRIMP]


def test_ingredient_without_probability_sorts_by_id() -> None:
    req = request(
        HALAL,
        confirmed_results=[
            confirmed(CHICKEN, ["is_pork"], "unknown"),
            confirmed(PORK, ["is_pork"], "unknown"),
        ],
    )
    assert [c.ingredient for c in cards(req)] == [PORK, CHICKEN]


def test_web_ingredient_without_id_uses_name_in_card_id() -> None:
    web_pork = IngredientRef(ingredient_id=None, canonical_name="삼겹살")
    req = request(HALAL, probability_results=[probability(web_pork, ["is_pork"], 0.7)])
    assert cards(req)[0].card_id == "card:scan-123:0:name-삼겹살:r1"


def test_18_related_unanswered_flag_gets_flag_card() -> None:
    req = request(FISH_ALLERGY, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_unclear_jeotgal"])
    [card] = cards(req)
    assert card.ingredient is None
    assert card.ambiguity_flag == "has_unclear_jeotgal"
    assert card.constraint_tags == ["is_fish"]
    assert card.reason_codes == ["ambiguous_menu"]
    assert card.card_id == "card:scan-123:0:flag-has_unclear_jeotgal-is_fish:r1"
    assert card.question["ko"] == "이 메뉴의 젓갈에 생선이 들어가나요?"


def test_19_unrelated_flag_gets_no_card() -> None:
    req = request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_unclear_jeotgal"])
    assert cards(req) == []


def test_20_21_answered_flag_gets_no_card() -> None:
    for answer in (flag_answer(["is_fish", "is_shrimp"], "absent"), flag_answer(["is_fish"], "present")):
        req = request(
            FISH_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            flags=["has_unclear_jeotgal"],
            flag_answers=[answer],
        )
        assert cards(req) == []


def test_22_answer_about_other_tag_asks_users_tag() -> None:
    req = request(
        SHRIMP_ALLERGY,
        confirmed_results=KIMCHI_STEW_CONFIRMED,
        flags=["has_unclear_jeotgal"],
        flag_answers=[flag_answer(["is_fish"], "absent")],
    )
    [card] = cards(req)
    assert card.constraint_tags == ["is_shrimp"]
    assert card.evidence_refs == ["evidence-flag"]


def test_23_mixed_present_answer_is_asked_again() -> None:
    req = request(
        FISH_ALLERGY,
        confirmed_results=KIMCHI_STEW_CONFIRMED,
        flags=["has_unclear_jeotgal"],
        flag_answers=[flag_answer(["is_fish", "is_shrimp"], "present")],
    )
    assert [c.ambiguity_flag for c in cards(req)] == ["has_unclear_jeotgal"]


def test_expired_flag_answer_adds_expired_reason() -> None:
    req = request(
        FISH_ALLERGY,
        confirmed_results=KIMCHI_STEW_CONFIRMED,
        flags=["has_unclear_jeotgal"],
        flag_answers=[flag_answer(["is_fish"], "absent", eligible=False)],
    )
    assert cards(req)[0].reason_codes == ["expired", "ambiguous_menu"]


def test_variant_flag_asks_about_every_forbidden_tag() -> None:
    req = request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_variant"])
    [card] = cards(req)
    assert card.constraint_tags == ["is_alcohol", "is_pork"]
    assert card.question["ko"] == "이 메뉴에 술이나 돼지고기가 들어가나요?"


def test_27_no_information_gets_one_menu_card() -> None:
    req = request(HALAL, no_information=True, information_status="none")
    [card] = cards(req)
    assert card.card_id == "card:scan-123:0:menu:r1"
    assert (card.ingredient, card.ambiguity_flag) == (None, None)
    assert card.constraint_tags == ["is_alcohol", "is_pork"]
    assert card.reason_codes == ["no_information"]
    assert (card.round, card.previous_question_id) == (1, None)


def test_no_ingredients_counts_as_no_information() -> None:
    [card] = cards(request(HALAL))
    assert card.reason_codes == ["no_information"]


def test_28_no_information_without_restriction_gets_no_card() -> None:
    assert cards(request(NO_RESTRICTION, no_information=True, information_status="none")) == []


@pytest.mark.parametrize("no_information", [False, True])
def test_33_34_storeless_scan_gets_no_card(no_information: bool) -> None:
    req = request(
        HALAL,
        probability_results=[probability(PORK, ["is_pork"], 0.8)],
        no_information=no_information,
        store_id=None,
    )
    assert cards(req) == []


def test_29_needs_third_asks_round_three() -> None:
    req = request(
        FISH_ALLERGY,
        probability_results=[probability(FISH_SAUCE, ["is_fish"], 0.7)],
        states=[state(FISH_SAUCE, "needs_third", 2)],
    )
    [card] = cards(req)
    assert (card.round, card.previous_question_id) == (3, "question-002")
    assert card.card_id == "card:scan-123:0:ingredient-110:r3"


def test_needs_second_asks_round_two() -> None:
    req = request(
        FISH_ALLERGY,
        probability_results=[probability(FISH_SAUCE, ["is_fish"], 0.7)],
        states=[state(FISH_SAUCE, "needs_second", 1, question_id="question-001")],
    )
    assert (cards(req)[0].round, cards(req)[0].previous_question_id) == (2, "question-001")


def test_30_expired_confirmed_answer_starts_round_one() -> None:
    req = request(
        HALAL,
        confirmed_results=[expired(PORK, ["is_pork"])],
        states=[state(PORK, "confirmed", 2)],
    )
    [card] = cards(req)
    assert (card.round, card.previous_question_id) == (1, None)
    assert card.reason_codes == ["expired"]


def test_state_of_other_target_is_ignored() -> None:
    req = request(
        HALAL,
        probability_results=[probability(PORK, ["is_pork"], 0.7)],
        states=[state(BEEF, "needs_third", 2)],
    )
    assert cards(req)[0].round == 1


def test_flag_round_needs_same_flag_and_tags() -> None:
    def flag_state(tags: list[str]) -> dict:
        return state(None, "needs_second", 1, flag="has_unclear_jeotgal", tags=tags)

    base = dict(confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_unclear_jeotgal"])
    same = cards(request(FISH_ALLERGY, states=[flag_state(["is_fish"])], **base))
    other = cards(request(FISH_ALLERGY, states=[flag_state(["is_shrimp"])], **base))
    assert same[0].round == 2
    assert other[0].round == 1


def test_card_ids_are_stable_across_runs() -> None:
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.7)])
    assert cards(req) == cards(req)


def test_english_user_gets_korean_and_english() -> None:
    req = request(
        HALAL,
        probability_results=[probability(PORK, ["is_pork"], 0.7)],
        localized_names={101: "pork"},
    )
    assert cards(req)[0].question == {
        "ko": "이 메뉴에 돼지고기가 들어가나요?",
        "en": "Does this dish contain pork?",
    }


def test_korean_user_gets_one_question() -> None:
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.7)], locale="ko")
    assert cards(req)[0].question == {"ko": "이 메뉴에 돼지고기가 들어가나요?"}


def test_other_language_gets_korean_and_that_language() -> None:
    req = request(
        HALAL,
        probability_results=[probability(PORK, ["is_pork"], 0.7)],
        localized_names={101: "豚肉"},
        locale="ja",
    )
    assert cards(req)[0].question == {
        "ko": "이 메뉴에 돼지고기가 들어가나요?",
        "ja": "この料理に豚肉は入っていますか？",
    }


def test_missing_translation_keeps_korean_ingredient_name() -> None:
    req = request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.7)], locale="zh-CN")
    assert cards(req)[0].question["zh-CN"] == "这道菜里有돼지고기吗？"


@pytest.mark.parametrize("locale", get_args(Locale))
def test_every_locale_has_every_template(locale: str) -> None:
    assert all(locale in names for names in TAG_NAMES.values())
    assert all(place is None or locale in place for place in FLAG_PLACES.values())
    assert locale in LISTS and locale in DISH_QUESTIONS and locale in PLACE_QUESTIONS
    req = request(
        profile(religion_type="halal", allergies=["is_fish"]),
        confirmed_results=KIMCHI_STEW_CONFIRMED,
        probability_results=[probability(PORK, ["is_pork"], 0.7)],
        flags=["has_unclear_jeotgal"],
        locale=locale,
    )
    for card in cards(req):
        assert set(card.question) == {"ko", locale}
        assert all(text.strip() for text in card.question.values())


def test_korean_particle_follows_final_consonant() -> None:
    req = request(
        FISH_ALLERGY,
        probability_results=[probability(FISH_SAUCE, ["is_fish"], 0.7)],
        locale="ko",
    )
    assert cards(req)[0].question["ko"] == "이 메뉴에 액젓이 들어가나요?"


def test_english_flag_question_lists_tags() -> None:
    req = request(
        profile(allergies=["is_fish", "is_shrimp", "is_crab"]),
        confirmed_results=KIMCHI_STEW_CONFIRMED,
        flags=["has_unclear_jeotgal"],
    )
    assert cards(req)[0].question == {
        "ko": "이 메뉴의 젓갈에 게, 생선이나 새우가 들어가나요?",
        "en": "Does the salted seafood (jeotgal) in this dish contain crab, fish or shrimp?",
    }
