"""Cases from docs/3-tool-exact.md §5. Supervisor-side cases (6-a, 14) live with ⓪."""

from datetime import datetime, timezone

import pytest

from ai_exact_feedback.tool import (
    StoreIdRequiredError,
    evaluate_exact_feedback,
    find_candidate_knowledge,
)
from ai_supervisor.contracts.external import (
    ExactFeedbackSlice,
    ItemKnowledge,
    MenuIngredientCache,
)

STORE_ID = 123456
RECENT = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
YEAR_AGO = datetime(2025, 10, 8, 3, 0, tzinfo=timezone.utc)

PORK, KIMCHI, TOFU, ONION, GARLIC = 101, 109, 120, 130, 140
NAMES = {PORK: "돼지고기", KIMCHI: "김치", TOFU: "두부", ONION: "양파", GARLIC: "마늘"}


def evidence(observed_at: datetime = RECENT, evidence_id: str = "evidence-001") -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_class": "hard",
        "source_type": "owner_feedback",
        "source_ref": f"ingredient_confirmations:{evidence_id}",
        "verification_status": "owner_confirmed",
        "reliability_weight": 1.0,
        "observed_at": observed_at.isoformat(),
    }


def confirmation(
    ingredient_id: int,
    status: str = "present",
    *,
    flagged_anomaly: bool = False,
    observed_at: datetime = RECENT,
    sent_expired: bool = False,
    sent_override_eligible: bool = True,
) -> dict:
    return {
        "ingredient": {"ingredient_id": ingredient_id, "canonical_name": NAMES[ingredient_id]},
        "constraint_tags": [],
        "status": status,
        "evidence": evidence(observed_at, f"evidence-{ingredient_id}"),
        "flagged_anomaly": flagged_anomaly,
        "override_eligible": sent_override_eligible,
        "expired": sent_expired,
    }


def flag_confirmation(observed_at: datetime = RECENT) -> dict:
    return {
        "ambiguity_flag": "has_unclear_jeotgal",
        "constraint_tags": ["is_fish"],
        "status": "absent",
        "evidence": evidence(observed_at, "evidence-flag"),
        "override_eligible": True,
        "expired": False,
    }


def exact_slice(
    confirmations: list[dict] | None = None,
    flags: list[dict] | None = None,
    menu_id: int = 1001,
) -> ExactFeedbackSlice:
    return ExactFeedbackSlice.model_validate(
        {
            "status": "ok",
            "exact": {
                "menu_id": menu_id,
                "confirmations": confirmations or [],
                "flag_confirmations": flags or [],
                "owner_question_states": [],
            },
        }
    )


def cache(*ingredient_ids: int) -> MenuIngredientCache:
    return MenuIngredientCache(
        menu_revision=7,
        ingredients_snapshot=[
            {"ingredient": {"ingredient_id": i, "canonical_name": NAMES[i]}}
            for i in ingredient_ids
        ],
    )


def evaluate(slice_: ExactFeedbackSlice, cached: MenuIngredientCache | None = None):
    return evaluate_exact_feedback(STORE_ID, slice_, cached)


# 1, 2, 3 — empty result, partial confirmation, 3-State


def test_01_no_records_returns_empty_unknown() -> None:
    outcome = evaluate(ExactFeedbackSlice(status="not_found", exact=None))
    assert outcome.confirmations == []
    assert outcome.completeness == "unknown"


def test_01_unavailable_slice_is_not_read_as_confirmed() -> None:
    outcome = evaluate(ExactFeedbackSlice(status="unavailable", exact=None), cache(PORK))
    assert outcome.confirmations == []
    assert outcome.completeness == "unknown"


def test_02_partial_confirmation_keeps_only_confirmed_ingredients() -> None:
    outcome = evaluate(
        exact_slice([confirmation(PORK), confirmation(KIMCHI)]),
        cache(PORK, KIMCHI, TOFU, ONION, GARLIC),
    )
    assert [c.ingredient.ingredient_id for c in outcome.confirmations] == [PORK, KIMCHI]
    assert outcome.completeness == "unknown"


def test_03_absent_stays_absent() -> None:
    outcome = evaluate(exact_slice([confirmation(PORK, "absent")]))
    assert outcome.confirmations[0].status == "absent"


# 4 — variant menus read only their own menu_id


def test_04_reads_only_requested_menu_slice() -> None:
    def slice_dict(menu_id: int, ingredient_id: int) -> dict:
        return {
            "menu_id": menu_id,
            "exact_feedback": exact_slice([confirmation(ingredient_id)], menu_id=menu_id).model_dump(
                mode="json"
            ),
            "ontology": {
                "status": "not_found",
                "menu": None,
                "recipe_ingredients": [],
                "menu_recipe_corpora": [],
                "menu_ingredient_priors": [],
                "cache": None,
            },
            "priors": {"status": "not_found", "store": [], "evidence": []},
        }

    item = ItemKnowledge.model_validate(
        {
            "item_id": "scan-123:0",
            "candidate_lookup": {
                "status": "ok",
                "candidates": [
                    {
                        "menu_id": 2002,
                        "menu_category_id": None,
                        "canonical_name": "차돌된장찌개",
                        "score": 0.9,
                        "match_type": "canonical",
                    },
                    {
                        "menu_id": 1001,
                        "menu_category_id": None,
                        "canonical_name": "된장찌개",
                        "score": 0.5,
                        "match_type": "fuzzy",
                    },
                ],
            },
            "candidate_knowledge": [slice_dict(2002, TOFU), slice_dict(1001, PORK)],
            "fallback_ontology": {
                "status": "not_found",
                "menu": None,
                "recipe_ingredients": [],
                "menu_recipe_corpora": [],
                "menu_ingredient_priors": [],
                "cache": None,
            },
            "web_search_cache": {"status": "not_found", "cached_sources": [], "next_rank": 1},
        }
    )
    knowledge = find_candidate_knowledge(item, 2002)
    assert knowledge is not None
    outcome = evaluate(knowledge.exact_feedback)
    assert [c.ingredient.ingredient_id for c in outcome.confirmations] == [TOFU]
    assert find_candidate_knowledge(item, 3003) is None


# 6 — store_id is mandatory


@pytest.mark.parametrize("store_id", [None, 0, -1])
def test_06_missing_store_id_raises(store_id: int | None) -> None:
    with pytest.raises(StoreIdRequiredError):
        evaluate_exact_feedback(store_id, exact_slice([confirmation(PORK)]), None)


# 7, 8, 9 — anomaly handling and completeness


def test_07_anomaly_is_kept_but_not_eligible() -> None:
    outcome = evaluate(exact_slice([confirmation(PORK, "absent", flagged_anomaly=True)]))
    assert len(outcome.confirmations) == 1
    assert outcome.confirmations[0].flagged_anomaly is True
    assert outcome.confirmations[0].override_eligible is False


def test_08_one_anomaly_blocks_complete() -> None:
    outcome = evaluate(
        exact_slice([confirmation(PORK, "absent", flagged_anomaly=True), confirmation(KIMCHI)]),
        cache(PORK, KIMCHI),
    )
    assert outcome.completeness == "unknown"


def test_09_cache_and_all_confirmed_is_complete() -> None:
    outcome = evaluate(
        exact_slice([confirmation(PORK), confirmation(KIMCHI, "absent")]), cache(PORK, KIMCHI)
    )
    assert outcome.completeness == "complete"


def test_09_without_cache_is_unknown() -> None:
    outcome = evaluate(exact_slice([confirmation(PORK), confirmation(KIMCHI)]), None)
    assert outcome.completeness == "unknown"


def test_09_confirmation_outside_cache_is_unknown() -> None:
    outcome = evaluate(exact_slice([confirmation(PORK), confirmation(TOFU)]), cache(PORK))
    assert outcome.completeness == "unknown"


def test_09_empty_snapshot_is_unknown() -> None:
    assert evaluate(exact_slice([]), cache()).completeness == "unknown"


def test_09_unreadable_snapshot_is_unknown() -> None:
    broken = MenuIngredientCache(menu_revision=7, ingredients_snapshot=[{"name": "돼지고기"}])
    assert evaluate(exact_slice([confirmation(PORK)]), broken).completeness == "unknown"


# 10, 11 — owner answers never expire (§1-7)


def test_10_year_old_confirmation_still_overrides() -> None:
    outcome = evaluate(exact_slice([confirmation(PORK, "absent", observed_at=YEAR_AGO)]))
    c = outcome.confirmations[0]
    assert (c.expired, c.override_eligible, c.flagged_anomaly) == (False, True, False)


def test_10_backend_expiry_values_are_recomputed() -> None:
    sent = confirmation(KIMCHI, sent_expired=True, sent_override_eligible=False)
    outcome = evaluate(exact_slice([sent]))
    c = outcome.confirmations[0]
    assert (c.expired, c.override_eligible) == (False, True)


def test_11_old_confirmation_does_not_block_complete() -> None:
    outcome = evaluate(
        exact_slice([confirmation(PORK, observed_at=YEAR_AGO), confirmation(KIMCHI)]),
        cache(PORK, KIMCHI),
    )
    assert outcome.completeness == "complete"


# 12, 13 — tags are left to ④, "present" is never an anomaly by itself


def test_12_constraint_tags_are_always_empty() -> None:
    sent = confirmation(PORK)
    sent["constraint_tags"] = ["is_pork"]
    outcome = evaluate(exact_slice([sent]))
    assert outcome.confirmations[0].ingredient.ingredient_id == PORK
    assert outcome.confirmations[0].constraint_tags == []


def test_13_present_without_anomaly_flag_is_eligible() -> None:
    outcome = evaluate(exact_slice([confirmation(PORK, "present")]))
    assert outcome.confirmations[0].override_eligible is True


# 15, 16 — flag answers


def test_15_flag_answer_does_not_mix_with_confirmations() -> None:
    outcome = evaluate(exact_slice([confirmation(PORK)], flags=[flag_confirmation()]), cache(PORK))
    assert len(outcome.flag_confirmations) == 1
    assert [c.ingredient.ingredient_id for c in outcome.confirmations] == [PORK]
    assert outcome.completeness == "complete"


def test_16_old_flag_answer_still_overrides() -> None:
    outcome = evaluate(exact_slice(flags=[flag_confirmation(YEAR_AGO)]))
    flag = outcome.flag_confirmations[0]
    assert (flag.expired, flag.override_eligible, flag.status) == (False, True, "absent")

