"""Verdict cases from docs/8-agent-xai.md §7.

Message text, owner cards and high-risk review cases (5, 13~15, 17, 26~31, 33~34 card
parts) come with those features; only their verdict part is checked here.
"""

from datetime import datetime, timezone

import pytest

from ai_decision_policy import (
    ConfirmedResult,
    DecisionInput,
    ProbabilityResult,
    TagObservation,
    VariantIngredient,
    decide,
    forbidden_tags,
)
from ai_supervisor.contracts.common import (
    FlagConfirmation,
    IngredientRef,
    NodeWarning,
    UserProfile,
)

PORK = IngredientRef(ingredient_id=101, canonical_name="돼지고기")
MILK = IngredientRef(ingredient_id=102, canonical_name="우유")
TUNA = IngredientRef(ingredient_id=103, canonical_name="참치")
RICE = IngredientRef(ingredient_id=104, canonical_name="밥")
BEEF = IngredientRef(ingredient_id=120, canonical_name="소고기")
FISH_SAUCE = IngredientRef(ingredient_id=110, canonical_name="액젓")
KIMCHI = IngredientRef(ingredient_id=109, canonical_name="김치")


def profile(**overrides) -> UserProfile:
    data = {
        "religion_type": None,
        "is_vegetarian": False,
        "vegetarian_type": None,
        "no_alcohol": False,
        "allergies": [],
        "no_spicy": False,
    }
    data.update(overrides)
    return UserProfile(**data)


HALAL = profile(religion_type="halal")
VEGAN = profile(is_vegetarian=True, vegetarian_type="vegan")
FISH_ALLERGY = profile(allergies=["is_fish"])
SHRIMP_ALLERGY = profile(allergies=["is_shrimp"])
NO_SPICY = profile(no_spicy=True)


def confirmed(
    ingredient: IngredientRef,
    tags: list[str],
    status: str = "present",
    *,
    eligible: bool = True,
    expired: bool = False,
    role: str | None = None,
) -> ConfirmedResult:
    return ConfirmedResult(
        ingredient=ingredient,
        constraint_tags=tags,
        role=role,
        status=status,
        override_eligible=eligible,
        expired=expired,
        evidence_refs=[f"evidence-{ingredient.ingredient_id}"],
    )


def probability(
    ingredient: IngredientRef,
    tags: list[str],
    posterior_mean: float,
    *,
    anomaly_locked: bool = False,
    role: str | None = None,
) -> ProbabilityResult:
    return ProbabilityResult(
        ingredient=ingredient,
        constraint_tags=tags,
        role=role,
        posterior_mean=posterior_mean,
        confidence=0.55,
        prior_source="global",
        anomaly_locked=anomaly_locked,
        evidence_refs=[f"recipe:{ingredient.ingredient_id}"],
    )


def flag_answer(tags: list[str], status: str, *, eligible: bool = True) -> FlagConfirmation:
    return FlagConfirmation(
        ambiguity_flag="has_unclear_jeotgal",
        constraint_tags=tags,
        status=status,
        evidence={
            "evidence_id": "evidence-flag",
            "evidence_class": "hard",
            "source_type": "owner_feedback",
            "source_ref": "owner_flag_confirmations:1",
            "verification_status": "owner_confirmed",
            "reliability_weight": 1.0,
            "observed_at": datetime(2026, 10, 8, tzinfo=timezone.utc),
        },
        override_eligible=eligible,
        expired=not eligible,
    )


def warning(code: str) -> NodeWarning:
    return NodeWarning(code=code, node="ontology", item_id="scan-123:0", message=code, forces_caution=True)


KIMCHI_STEW_CONFIRMED = [confirmed(KIMCHI, [], "present"), confirmed(PORK, ["is_pork"], "absent")]


# Step 1 — profile mapping


def test_profile_mapping_merges_every_restriction() -> None:
    tags = forbidden_tags(
        profile(religion_type="hindu", no_alcohol=True, no_spicy=True, allergies=["is_egg"])
    )
    assert tags == {"is_beef", "is_alcohol", "is_spicy", "is_egg"}
    assert forbidden_tags(profile(is_vegetarian=True, vegetarian_type="pesco")) == {
        "is_pork",
        "is_beef",
        "is_chicken",
        "is_duck",
    }
    assert forbidden_tags(profile(religion_type="kosher")) == {
        "is_pork",
        "is_crab",
        "is_shrimp",
        "is_shellfish",
        "is_squid",
    }


# §7 cases


def test_01_confirmed_forbidden_ingredient_is_danger() -> None:
    outcome = decide(DecisionInput(HALAL, confirmed_results=[confirmed(PORK, ["is_pork"])]))
    assert (outcome.risk_level, outcome.evidence_basis) == ("danger", "confirmed")
    assert outcome.matched_tags == ["is_pork"]


def test_02_probability_only_is_caution_with_number() -> None:
    outcome = decide(DecisionInput(VEGAN, probability_results=[probability(MILK, ["is_milk"], 0.3)]))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].posterior_mean == 0.3


def test_03_no_information_is_caution() -> None:
    outcome = decide(DecisionInput(HALAL, no_information=True))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")


def test_04_variant_guess_never_becomes_danger() -> None:
    variant = VariantIngredient(BEEF, ["is_beef"], "identity", ["runtime:variant:0"])
    outcome = decide(DecisionInput(VEGAN, proposed_variant_ingredients=[variant]))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].status == "unknown"


def test_06_eligible_owner_answer_beats_probability() -> None:
    outcome = decide(
        DecisionInput(
            HALAL,
            confirmed_results=[confirmed(PORK, ["is_pork"], "absent")],
            probability_results=[probability(PORK, ["is_pork"], 0.9)],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("safe", "confirmed")
    assert outcome.risk_ingredients == []


def test_07_unknown_status_is_not_absent() -> None:
    outcome = decide(DecisionInput(HALAL, confirmed_results=[confirmed(PORK, ["is_pork"], "unknown")]))
    assert outcome.risk_level == "caution"


def test_08_ineligible_answer_does_not_cover_probability() -> None:
    outcome = decide(
        DecisionInput(
            HALAL,
            confirmed_results=[confirmed(PORK, ["is_pork"], "absent", eligible=False)],
            probability_results=[probability(PORK, ["is_pork"], 0.8)],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].posterior_mean == 0.8


def test_09_partial_information_is_never_safe() -> None:
    outcome = decide(
        DecisionInput(
            HALAL,
            probability_results=[probability(KIMCHI, [], 0.01)],
            information_status="partial",
        )
    )
    assert outcome.risk_level == "caution"
    assert outcome.information_status == "partial"


def test_09_partial_information_blocks_all_confirmed_safe() -> None:
    outcome = decide(
        DecisionInput(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, information_status="partial")
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")


def test_10_tag_lookup_failure_blocks_safe() -> None:
    outcome = decide(
        DecisionInput(
            HALAL,
            confirmed_results=[confirmed(PORK, [], "present")],
            warnings=[warning("constraint_tags_lookup_failed")],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")


def test_11_high_probability_alone_is_not_danger() -> None:
    outcome = decide(DecisionInput(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.95)]))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].posterior_mean == 0.95


def test_12_all_owner_confirmed_without_forbidden_is_safe() -> None:
    outcome = decide(DecisionInput(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED))
    assert (outcome.risk_level, outcome.evidence_basis) == ("safe", "confirmed")
    assert outcome.evidence_refs == ["evidence-109", "evidence-101"]


def test_13_role_is_kept_and_does_not_change_verdict() -> None:
    seafood = profile(allergies=["is_fish"])
    outcome = decide(
        DecisionInput(
            seafood,
            confirmed_results=[confirmed(TUNA, ["is_fish"], role="identity"), confirmed(RICE, [], "present")],
        )
    )
    assert outcome.risk_level == "danger"
    assert outcome.risk_ingredients[0].role == "identity"


def test_16_spicy_observation_blocks_safe_without_danger() -> None:
    observation = TagObservation("is_spicy", 0.78, ["ocr:scan-123:0:is_spicy"])
    outcome = decide(
        DecisionInput(NO_SPICY, confirmed_results=KIMCHI_STEW_CONFIRMED, risk_tag_observations=[observation])
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.matched_tags == ["is_spicy"]
    assert outcome.risk_ingredients == []


def test_18_related_unanswered_flag_blocks_safe() -> None:
    outcome = decide(
        DecisionInput(FISH_ALLERGY, confirmed_results=KIMCHI_STEW_CONFIRMED, menu_ambiguity_flags=["has_unclear_jeotgal"])
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")
    assert [(f.flag, f.ask_tags, f.state) for f in outcome.flag_checks] == [
        ("has_unclear_jeotgal", ["is_fish"], "unresolved")
    ]


def test_19_unrelated_flag_does_not_block_safe() -> None:
    outcome = decide(
        DecisionInput(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, menu_ambiguity_flags=["has_unclear_jeotgal"])
    )
    assert outcome.risk_level == "safe"
    assert outcome.flag_checks == []


def test_20_absent_answer_covering_asked_tags_resolves_flag() -> None:
    outcome = decide(
        DecisionInput(
            FISH_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            menu_ambiguity_flags=["has_unclear_jeotgal"],
            flag_confirmations=[flag_answer(["is_fish", "is_shrimp"], "absent")],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("safe", "confirmed")


def test_21_present_answer_with_forbidden_tags_is_danger() -> None:
    outcome = decide(
        DecisionInput(
            FISH_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            menu_ambiguity_flags=["has_unclear_jeotgal"],
            flag_confirmations=[flag_answer(["is_fish"], "present")],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("danger", "confirmed")
    assert outcome.matched_tags == ["is_fish"]


def test_22_answer_about_other_tag_does_not_resolve() -> None:
    outcome = decide(
        DecisionInput(
            SHRIMP_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            menu_ambiguity_flags=["has_unclear_jeotgal"],
            flag_confirmations=[flag_answer(["is_fish"], "absent")],
        )
    )
    assert outcome.risk_level == "caution"
    assert outcome.flag_checks[0].ask_tags == ["is_shrimp"]


def test_23_present_answer_with_unknown_tag_mix_is_not_danger() -> None:
    outcome = decide(
        DecisionInput(
            FISH_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            menu_ambiguity_flags=["has_unclear_jeotgal"],
            flag_confirmations=[flag_answer(["is_fish", "is_shrimp"], "present")],
        )
    )
    assert outcome.risk_level == "caution"


def test_expired_flag_answer_does_not_resolve() -> None:
    outcome = decide(
        DecisionInput(
            FISH_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            menu_ambiguity_flags=["has_unclear_jeotgal"],
            flag_confirmations=[flag_answer(["is_fish"], "absent", eligible=False)],
        )
    )
    assert outcome.risk_level == "caution"


def test_24_anomaly_locked_stays_caution_and_listed() -> None:
    outcome = decide(
        DecisionInput(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.95, anomaly_locked=True)])
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].anomaly_locked is True


def test_25_unrelated_anomaly_still_blocks_safe() -> None:
    outcome = decide(
        DecisionInput(
            FISH_ALLERGY,
            confirmed_results=[confirmed(KIMCHI, [], "present")],
            probability_results=[probability(PORK, ["is_pork"], 0.95, anomaly_locked=True)],
        )
    )
    assert outcome.risk_level == "caution"
    assert outcome.risk_ingredients == []


def test_32_no_ingredients_is_never_safe() -> None:
    outcome = decide(DecisionInput(HALAL))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")


@pytest.mark.parametrize("no_information", [False, True])
def test_33_34_storeless_scan_is_never_safe(no_information: bool) -> None:
    outcome = decide(
        DecisionInput(
            HALAL,
            probability_results=[] if no_information else [probability(PORK, ["is_pork"], 0.8)],
            no_information=no_information,
            owner_interaction_allowed=False,
            warnings=[warning("store_context_missing")],
        )
    )
    assert outcome.risk_level == "caution"


def test_storeless_flag_blocks_even_confirmed_only_input() -> None:
    outcome = decide(
        DecisionInput(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, owner_interaction_allowed=False)
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")


def test_localized_name_falls_back_to_korean() -> None:
    outcome = decide(
        DecisionInput(
            HALAL,
            probability_results=[
                probability(PORK, ["is_pork"], 0.6),
                probability(FISH_SAUCE, ["is_pork"], 0.2),
                probability(IngredientRef(ingredient_id=None, canonical_name="돼지 육수"), ["is_pork"], 0.4),
            ],
            localized_names={101: "pork"},
        )
    )
    assert [r.localized_name for r in outcome.risk_ingredients] == ["pork", "액젓", "돼지 육수"]
