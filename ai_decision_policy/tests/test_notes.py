"""Message note choices from docs/8-agent-xai.md §7 (13~15, 18) and §4 `notes`.

Only the note codes and their subjects; the text per locale is in test_messages.py.
"""

from ai_decision_policy import decide, select_notes
from ai_decision_policy.tests.test_decision_policy import (
    FISH_ALLERGY,
    KIMCHI,
    KIMCHI_STEW_CONFIRMED,
    RICE,
    SHRIMP_ALLERGY,
    TUNA,
    confirmed,
    probability,
    profile,
    request,
)
from ai_supervisor.contracts.common import IngredientRef

SHRIMP = IngredientRef(ingredient_id=130, canonical_name="새우")
ANCHOVY = IngredientRef(ingredient_id=131, canonical_name="멸치")
SEAFOOD_ALLERGY = profile(allergies=["is_fish", "is_shrimp"])


def codes(notes) -> list[tuple]:
    return [
        (n.code, n.ambiguity_flag, n.ingredient.canonical_name if n.ingredient else None, n.role)
        for n in notes
    ]


def test_13_identity_ingredient_gets_hard_to_remove_note() -> None:
    outcome = decide(
        request(
            SEAFOOD_ALLERGY,
            confirmed_results=[confirmed(TUNA, ["is_fish"], role="identity"), confirmed(RICE, [], "present")],
        )
    )
    assert outcome.risk_level == "danger"
    assert codes(select_notes(outcome)) == [("hard_to_remove", None, "참치", "identity")]


def test_14_hidden_ingredient_from_probability_gets_note() -> None:
    outcome = decide(
        request(
            SHRIMP_ALLERGY,
            confirmed_results=[confirmed(KIMCHI, [], "present")],
            probability_results=[probability(SHRIMP, ["is_shrimp"], 0.4, role="hidden")],
        )
    )
    assert (outcome.risk_level, outcome.evidence_basis) == ("caution", "estimated")
    assert codes(select_notes(outcome)) == [("hard_to_remove", None, "새우", "hidden")]


def test_15_component_or_unknown_role_gets_no_note() -> None:
    outcome = decide(
        request(
            SEAFOOD_ALLERGY,
            probability_results=[
                probability(SHRIMP, ["is_shrimp"], 0.4, role="component"),
                probability(ANCHOVY, ["is_fish"], 0.3),
            ],
        )
    )
    assert len(outcome.risk_ingredients) == 2
    assert select_notes(outcome) == []


def test_note_is_only_for_ingredients_that_hit_the_user() -> None:
    # Tuna is the main ingredient but this user only avoids shrimp.
    outcome = decide(
        request(
            SHRIMP_ALLERGY,
            confirmed_results=[confirmed(TUNA, ["is_fish"], role="identity"), confirmed(RICE, [], "present")],
        )
    )
    assert outcome.risk_ingredients == []
    assert select_notes(outcome) == []


def test_18_unresolved_flag_gets_ambiguous_menu_note() -> None:
    outcome = decide(
        request(FISH_ALLERGY, confirmed_results=KIMCHI_STEW_CONFIRMED, flags=["has_unclear_jeotgal"])
    )
    assert codes(select_notes(outcome)) == [("ambiguous_menu", "has_unclear_jeotgal", None, None)]


def test_flag_notes_come_before_ingredient_notes() -> None:
    outcome = decide(
        request(
            SEAFOOD_ALLERGY,
            confirmed_results=[confirmed(KIMCHI, [], "present")],
            probability_results=[
                probability(TUNA, ["is_fish"], 0.6, role="identity"),
                probability(SHRIMP, ["is_shrimp"], 0.4, role="hidden"),
            ],
            flags=["has_unclear_jeotgal"],
        )
    )
    assert codes(select_notes(outcome)) == [
        ("ambiguous_menu", "has_unclear_jeotgal", None, None),
        ("hard_to_remove", None, "참치", "identity"),
        ("hard_to_remove", None, "새우", "hidden"),
    ]


def test_notes_do_not_change_the_verdict() -> None:
    plain = request(SHRIMP_ALLERGY, probability_results=[probability(SHRIMP, ["is_shrimp"], 0.4)])
    hidden = request(SHRIMP_ALLERGY, probability_results=[probability(SHRIMP, ["is_shrimp"], 0.4, role="hidden")])
    a, b = decide(plain), decide(hidden)
    assert (a.risk_level, a.evidence_basis) == (b.risk_level, b.evidence_basis)
