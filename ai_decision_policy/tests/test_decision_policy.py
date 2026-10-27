"""Verdict and high-risk review cases from docs/8-agent-xai.md §7.

Owner cards are in test_owner_cards.py, note choices in test_notes.py and message
text in test_messages.py; for cases that also check text (13~15, 31) only the
verdict part is covered here.
"""

from datetime import datetime, timezone

import pytest

from ai_decision_policy import decide, forbidden_tags
from ai_supervisor.contracts.common import (
    FlagConfirmation,
    IngredientRef,
    NodeWarning,
    UserProfile,
)
from ai_supervisor.contracts.decision import (
    ConfirmedResult,
    DecisionProbabilityResult,
    DecisionRequest,
    DecisionTagObservation,
    ProposedVariantIngredient,
)

STORE_ID = 123456
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
NO_RESTRICTION = profile()


def confirmed(
    ingredient: IngredientRef,
    tags: list[str],
    status: str = "present",
    *,
    eligible: bool = True,
    role: str | None = None,
) -> ConfirmedResult:
    return ConfirmedResult(
        ingredient=ingredient,
        constraint_tags=tags,
        role=role,
        status=status,
        override_eligible=eligible,
        expired=False,
        evidence_refs=[f"evidence-{ingredient.ingredient_id}"],
    )


def probability(
    ingredient: IngredientRef,
    tags: list[str],
    posterior_mean: float,
    *,
    anomaly_locked: bool = False,
    role: str | None = None,
) -> DecisionProbabilityResult:
    return DecisionProbabilityResult(
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
        expired=False,
    )


def warning(code: str) -> NodeWarning:
    return NodeWarning(code=code, node="ontology", item_id="scan-123:0", message=code, forces_caution=True)


def request(
    user: UserProfile,
    *,
    confirmed_results: list[ConfirmedResult] | None = None,
    probability_results: list[DecisionProbabilityResult] | None = None,
    variants: list[ProposedVariantIngredient] | None = None,
    observations: list[DecisionTagObservation] | None = None,
    flags: list[str] | None = None,
    flag_answers: list[FlagConfirmation] | None = None,
    no_information: bool = False,
    information_status: str = "complete",
    store_id: int | None = STORE_ID,
    localized_names: dict[int, str] | None = None,
    warnings: list[NodeWarning] | None = None,
    states: list[dict] | None = None,
    locale: str = "en",
) -> DecisionRequest:
    warnings = list(warnings or [])
    if store_id is None:
        warnings.append(warning("store_context_missing"))
    return DecisionRequest(
        context={
            "schema_version": "3.0",
            "trace_id": "0199a2f0-0000-7000-8000-000000000001",
            "scan_session_id": "scan-123",
            "store_id": store_id,
            "call_scope": "item",
            "item_id": "scan-123:0",
        },
        menu={"menu_id": 1001, "menu_category_id": None, "normalized_menu_name": "김치찌개"},
        confirmed_results=confirmed_results or [],
        probability_results=probability_results or [],
        proposed_variant_ingredients=variants or [],
        risk_tag_observations=observations or [],
        menu_ambiguity_flags=flags or [],
        flag_confirmations=flag_answers or [],
        owner_question_states=states or [],
        no_information=no_information,
        information_status=information_status,
        owner_interaction_allowed=store_id is not None,
        user_profile=user,
        locale=locale,
        localized_names=[
            {"ingredient_id": i, "localized_name": n} for i, n in (localized_names or {}).items()
        ],
        warnings=warnings,
        errors=[],
    )


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



MEAT_TAGS = {"is_pork", "is_beef", "is_chicken", "is_duck"}


@pytest.mark.parametrize("vegetarian_type", ["vegan", "lacto", "ovo", "lacto_ovo", "pesco"])
def test_every_vegetarian_type_forbids_all_meat(vegetarian_type: str) -> None:
    tags = forbidden_tags(profile(is_vegetarian=True, vegetarian_type=vegetarian_type))
    assert MEAT_TAGS <= tags


@pytest.mark.parametrize(
    ("vegetarian_type", "allowed"),
    [
        ("vegan", set()),
        ("lacto", {"is_milk"}),
        ("ovo", {"is_egg"}),
        ("lacto_ovo", {"is_milk", "is_egg"}),
    ],
)
def test_vegetarian_types_differ_only_in_milk_and_egg(
    vegetarian_type: str, allowed: set[str]
) -> None:
    tags = forbidden_tags(profile(is_vegetarian=True, vegetarian_type=vegetarian_type))
    assert {"is_fish", "is_shrimp"} <= tags
    assert {"is_milk", "is_egg"} - allowed <= tags
    assert not allowed & tags


# §7 verdict cases


def test_01_confirmed_forbidden_ingredient_is_danger() -> None:
    outcome = decide(request(HALAL, confirmed_results=[confirmed(PORK, ["is_pork"])]))
    assert (outcome.risk_level, outcome.evidence_basis) == ("danger", "confirmed")
    assert outcome.matched_tags == ["is_pork"]


def test_02_probability_only_is_caution_with_number() -> None:
    outcome = decide(request(VEGAN, probability_results=[probability(MILK, ["is_milk"], 0.3)]))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].posterior_mean == 0.3


def test_03_no_information_is_caution() -> None:
    outcome = decide(request(HALAL, no_information=True))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")


def test_04_variant_guess_never_becomes_danger() -> None:
    variant = ProposedVariantIngredient(
        ingredient=BEEF,
        constraint_tags=["is_beef"],
        role="identity",
        source="variant_suggested",
        evidence_refs=["runtime:variant:0"],
    )
    outcome = decide(request(VEGAN, variants=[variant]))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].status == "unknown"
    assert outcome.risk_ingredients[0].role == "identity"


def test_06_eligible_owner_answer_beats_probability() -> None:
    outcome = decide(
        request(
            HALAL,
            confirmed_results=[confirmed(PORK, ["is_pork"], "absent")],
            probability_results=[probability(PORK, ["is_pork"], 0.9)],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("safe", "confirmed")
    assert outcome.risk_ingredients == []


def test_07_unknown_status_is_not_absent() -> None:
    outcome = decide(request(HALAL, confirmed_results=[confirmed(PORK, ["is_pork"], "unknown")]))
    assert outcome.risk_level == "caution"


def test_08_ineligible_answer_does_not_cover_probability() -> None:
    outcome = decide(
        request(
            HALAL,
            confirmed_results=[confirmed(PORK, ["is_pork"], "absent", eligible=False)],
            probability_results=[probability(PORK, ["is_pork"], 0.8)],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].posterior_mean == 0.8


def test_09_partial_information_is_never_safe() -> None:
    outcome = decide(
        request(HALAL, probability_results=[probability(KIMCHI, [], 0.01)], information_status="partial")
    )
    assert outcome.risk_level == "caution"
    assert outcome.information_status == "partial"


def test_09_partial_information_blocks_all_confirmed_safe() -> None:
    outcome = decide(request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, information_status="partial"))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")


def test_10_tag_lookup_failure_blocks_safe() -> None:
    outcome = decide(
        request(
            HALAL,
            confirmed_results=[confirmed(PORK, [], "present")],
            warnings=[warning("constraint_tags_lookup_failed")],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")


def test_11_high_probability_alone_is_not_danger() -> None:
    outcome = decide(request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.95)]))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].posterior_mean == 0.95


def test_12_all_owner_confirmed_without_forbidden_is_safe() -> None:
    outcome = decide(request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED))
    assert (outcome.risk_level, outcome.evidence_basis) == ("safe", "confirmed")
    assert outcome.evidence_refs == ["evidence-109", "evidence-101"]


def test_13_role_is_kept_and_does_not_change_verdict() -> None:
    outcome = decide(
        request(
            FISH_ALLERGY,
            confirmed_results=[confirmed(TUNA, ["is_fish"], role="identity"), confirmed(RICE, [], "present")],
        )
    )
    assert outcome.risk_level == "danger"
    assert outcome.risk_ingredients[0].role == "identity"


def test_16_spicy_observation_blocks_safe_without_danger() -> None:
    observation = DecisionTagObservation(tag="is_spicy", confidence=0.78, evidence_refs=["ocr:scan-123:0:is_spicy"])
    outcome = decide(request(NO_SPICY, confirmed_results=KIMCHI_STEW_CONFIRMED, observations=[observation]))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.matched_tags == ["is_spicy"]
    assert outcome.risk_ingredients == []


def test_18_related_unanswered_flag_blocks_safe() -> None:
    outcome = decide(
        request(FISH_ALLERGY, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_unclear_jeotgal"])
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")
    assert [(f.flag, f.ask_tags, f.state) for f in outcome.flag_checks] == [
        ("has_unclear_jeotgal", ["is_fish"], "unresolved")
    ]


def test_19_unrelated_flag_does_not_block_safe() -> None:
    outcome = decide(request(HALAL, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_unclear_jeotgal"]))
    assert outcome.risk_level == "safe"
    assert outcome.flag_checks == []


def test_20_absent_answer_covering_asked_tags_resolves_flag() -> None:
    outcome = decide(
        request(
            FISH_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            flags=["has_unclear_jeotgal"],
            flag_answers=[flag_answer(["is_fish", "is_shrimp"], "absent")],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("safe", "confirmed")


def test_21_present_answer_with_forbidden_tags_is_danger() -> None:
    outcome = decide(
        request(
            FISH_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            flags=["has_unclear_jeotgal"],
            flag_answers=[flag_answer(["is_fish"], "present")],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("danger", "confirmed")
    assert outcome.matched_tags == ["is_fish"]


def test_22_answer_about_other_tag_does_not_resolve() -> None:
    outcome = decide(
        request(
            SHRIMP_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            flags=["has_unclear_jeotgal"],
            flag_answers=[flag_answer(["is_fish"], "absent")],
        )
    )
    assert outcome.risk_level == "caution"
    assert outcome.flag_checks[0].ask_tags == ["is_shrimp"]


def test_23_present_answer_with_unknown_tag_mix_is_not_danger() -> None:
    outcome = decide(
        request(
            FISH_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            flags=["has_unclear_jeotgal"],
            flag_answers=[flag_answer(["is_fish", "is_shrimp"], "present")],
        )
    )
    assert outcome.risk_level == "caution"


def test_ineligible_flag_answer_does_not_resolve() -> None:
    outcome = decide(
        request(
            FISH_ALLERGY,
            confirmed_results=KIMCHI_STEW_CONFIRMED,
            flags=["has_unclear_jeotgal"],
            flag_answers=[flag_answer(["is_fish"], "absent", eligible=False)],
        )
    )
    assert outcome.risk_level == "caution"


def test_24_anomaly_locked_stays_caution_and_needs_review() -> None:
    outcome = decide(
        request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.95, anomaly_locked=True)])
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert outcome.risk_ingredients[0].anomaly_locked is True
    assert (outcome.high_risk_review.required, outcome.high_risk_review.reason_code) == (True, "anomaly")


def test_25_unrelated_anomaly_blocks_safe_without_review() -> None:
    outcome = decide(
        request(
            FISH_ALLERGY,
            confirmed_results=[confirmed(KIMCHI, [], "present")],
            probability_results=[probability(PORK, ["is_pork"], 0.95, anomaly_locked=True)],
        )
    )
    assert outcome.risk_level == "caution"
    assert outcome.risk_ingredients == []
    assert outcome.high_risk_review.required is False


def test_32_no_ingredients_is_never_safe() -> None:
    outcome = decide(request(HALAL))
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "unknown")


@pytest.mark.parametrize("no_information", [False, True])
def test_33_34_storeless_scan_is_never_safe_or_reviewed(no_information: bool) -> None:
    outcome = decide(
        request(
            HALAL,
            probability_results=[] if no_information else [probability(PORK, ["is_pork"], 0.8, anomaly_locked=True)],
            no_information=no_information,
            store_id=None,
        )
    )
    assert outcome.risk_level == "caution"
    assert (outcome.high_risk_review.required, outcome.high_risk_review.reason_code) == (False, None)


def test_localized_name_falls_back_to_korean() -> None:
    web_broth = IngredientRef(ingredient_id=None, canonical_name="돼지 육수")
    outcome = decide(
        request(
            HALAL,
            probability_results=[
                probability(PORK, ["is_pork"], 0.6),
                probability(FISH_SAUCE, ["is_pork"], 0.2),
                probability(web_broth, ["is_pork"], 0.4),
            ],
            localized_names={101: "pork"},
        )
    )
    assert [r.ingredient.localized_name for r in outcome.risk_ingredients] == ["pork", "액젓", "돼지 육수"]


# §2-3 high-risk review signals


def review(outcome) -> tuple[bool, str | None]:
    return outcome.high_risk_review.required, outcome.high_risk_review.reason_code


def test_review_no_information_needs_a_forbidden_tag() -> None:
    assert review(decide(request(HALAL, no_information=True))) == (True, "no_information")
    assert review(decide(request(NO_RESTRICTION, no_information=True))) == (False, None)


def test_review_empty_ingredients_counts_as_no_information() -> None:
    assert review(decide(request(HALAL))) == (True, "no_information")


def test_review_partial_information() -> None:
    outcome = decide(
        request(HALAL, probability_results=[probability(KIMCHI, [], 0.1)], information_status="partial")
    )
    assert review(outcome) == (True, "partial_information")


@pytest.mark.parametrize(
    "code", ["insufficient_sources", "web_search_deadline_exceeded", "web_llm_extraction_failed"]
)
def test_review_web_shortage(code: str) -> None:
    outcome = decide(request(HALAL, probability_results=[probability(KIMCHI, [], 0.1)], warnings=[warning(code)]))
    assert review(outcome) == (True, "insufficient_web_sources")


@pytest.mark.parametrize("code", ["unmapped_constraint_tags", "constraint_tags_lookup_failed"])
def test_review_unknown_tag_applies_even_without_restrictions(code: str) -> None:
    outcome = decide(
        request(NO_RESTRICTION, probability_results=[probability(KIMCHI, [], 0.1)], warnings=[warning(code)])
    )
    assert review(outcome) == (True, "unknown_tag")


def test_review_picks_highest_priority_reason() -> None:
    outcome = decide(
        request(
            HALAL,
            probability_results=[probability(PORK, ["is_pork"], 0.95, anomaly_locked=True)],
            information_status="partial",
            warnings=[warning("unmapped_constraint_tags"), warning("insufficient_sources")],
        )
    )
    assert review(outcome) == (True, "anomaly")


def test_review_not_needed_for_plain_estimate() -> None:
    outcome = decide(request(HALAL, probability_results=[probability(PORK, ["is_pork"], 0.6)]))
    assert review(outcome) == (False, None)
