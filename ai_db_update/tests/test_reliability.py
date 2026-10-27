import pytest

from ai_db_update import SourceClaim, build_commands, claims_for, reliability_updates
from ai_supervisor.contracts.bayesian import BayesianIngredient
from ai_supervisor.contracts.common import NodeContext
from ai_supervisor.contracts.db_update import DbUpdateRequest


def ingredient(*, source="recipe", obs=(), curated=False):
    observations = [{"corpus": c, "k_count": k, "n_total": n} for c, k, n in obs]
    return BayesianIngredient.model_validate(
        {
            "ingredient": {"ingredient_id": 4, "canonical_name": "돼지고기"},
            "constraint_tags": ["is_pork"],
            "role": None,
            "source": source,
            "depth": 0,
            "curated": curated,
            "observation_basis": "direct" if observations else "none",
            "observations": observations,
            "anomaly_locked": False,
            "evidence_refs": [],
        }
    )


def updates(claims, present=True, p=None):
    return {
        u.source_type: (u.correct_count_delta, u.comparison_count_delta)
        for u in reliability_updates(
            claims, present=present, recipe_probability=p, store_id=1, menu_id=1001, ingredient_id=4
        )
    }


def test_corpus_claim_uses_smoothed_share():
    claims = claims_for(ingredient(obs=(("10000recipe", 30, 57), ("semie", 0, 3))))
    assert claims == [SourceClaim("corpus:10000recipe", True), SourceClaim("corpus:semie", False)]


def test_listed_means_present_unlisted_is_no_claim():
    assert claims_for(ingredient(curated=True)) == [SourceClaim("curated", True)]
    assert claims_for(ingredient()) == []
    assert claims_for(ingredient(source="web_search", obs=(("10000recipe", 3, 10),))) == [
        SourceClaim("web_search", True)
    ]


def test_corpus_with_no_recipes_makes_no_claim():
    assert claims_for(ingredient(obs=(("wtable", 0, 0),))) == []


def test_agree_and_disagree_counts():  # ⑦ §7-11
    claims = [SourceClaim("corpus:10000recipe", True), SourceClaim("corpus:semie", False)]
    assert updates(claims, present=True) == {"corpus:10000recipe": (1, 1), "corpus:semie": (0, 1)}
    assert updates(claims, present=False, p=0.4) == {"corpus:10000recipe": (0, 1), "corpus:semie": (1, 1)}


def test_anomaly_answer_updates_nothing():  # ⑦ §7-12
    assert updates([SourceClaim("curated", True)], present=False, p=0.95) == {}
    assert updates([SourceClaim("curated", True)], present=True, p=0.95) == {"curated": (1, 1)}


def test_same_source_counted_once():
    claims = [SourceClaim("curated", True), SourceClaim("curated", True)]
    assert updates(claims) == {"curated": (1, 1)}


def test_commands_dedupe_on_store_menu_ingredient():
    # a later answer for the same ingredient yields the same key: the backend counts it once
    payloads = reliability_updates(
        [SourceClaim("curated", True)], present=True, recipe_probability=None,
        store_id=1, menu_id=1001, ingredient_id=4,
    )
    again = reliability_updates(
        [SourceClaim("curated", True)], present=False, recipe_probability=0.2,
        store_id=1, menu_id=1001, ingredient_id=4,
    )

    def key(payload):
        request = DbUpdateRequest(
            context=NodeContext(
                schema_version="3.0", trace_id="0199a2f0-0000-7000-8000-000000000001",
                scan_session_id="s", store_id=1, call_scope="out_of_band", item_id=None,
            ),
            operations=[{"operation_id": "op", "operation_type": "update_source_reliability", "payload": payload}],
            warnings=[], errors=[],
        )
        return build_commands(request).commands[0].idempotency_key

    assert key(payloads[0]) == key(again[0])


@pytest.mark.parametrize("agree, compared, expected", [(0, 0, 0.5), (9, 10, 0.83), (62, 62, 0.98), (0, 62, 0.02)])
def test_reliability_formula_table(agree, compared, expected):  # ⑦ §3-1 table, kept by the backend
    assert round((agree + 1) / (compared + 2), 2) == expected
