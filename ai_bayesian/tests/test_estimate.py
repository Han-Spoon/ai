import asyncio

import pytest

from ai_bayesian import BayesianTool, confidence_score, estimate_probabilities
from ai_supervisor.contracts.bayesian import BayesianRequest
from ai_supervisor.contracts.common import NodeContext, NodeWarning

ITEM_ID = "scan-123:0"


def ingredient(
    *,
    obs=(("10000recipe", 53, 57),),
    source="recipe",
    depth=0,
    basis=None,
    curated=False,
    locked=False,
    ingredient_id=10,
):
    observations = [{"corpus": c, "k_count": k, "n_total": n} for c, k, n in obs]
    return {
        "ingredient": {"ingredient_id": ingredient_id, "canonical_name": "마늘"},
        "constraint_tags": [],
        "role": None,
        "source": source,
        "depth": depth,
        "curated": curated,
        "observation_basis": basis or ("direct" if observations else "none"),
        "observations": observations,
        "anomaly_locked": locked,
        "evidence_refs": [],
    }


def request(
    ingredients,
    *,
    weights=(("corpus:10000recipe", 1.0),),
    store=(),
    store_id=123,
    priors_status=None,
    warnings=(),
):
    store_rows = [
        {"ingredient_id": i, "alpha": a, "beta": b, "evidence_refs": ["log:1"]} for i, a, b in store
    ]
    status = priors_status or ("ok" if store_rows else "not_found")
    return BayesianRequest(
        context=NodeContext(
            schema_version="3.0",
            trace_id="0199a2f0-0000-7000-8000-000000000001",
            scan_session_id="scan-123",
            store_id=store_id,
            call_scope="item",
            item_id=ITEM_ID,
        ),
        menu_category_id=None,
        ingredients=list(ingredients),
        priors={
            "status": status,
            "store": store_rows,
            "cluster": [],
            "global": [],
            "evidence": (
                [
                    {
                        "evidence_id": "log:1",
                        "evidence_class": "soft",
                        "source_type": "ocr_variant_tag",
                        "source_ref": "ingredient_evidence_log:1",
                        "verification_status": "approved",
                        "reliability_weight": 0.5,
                        "observed_at": "2026-10-01T00:00:00Z",
                    }
                ]
                if store_rows
                else []
            ),
        },
        source_reliability={
            "status": "ok",
            "weights": [{"source_key": k, "weight": w} for k, w in weights],
        },
        warnings=list(warnings),
        errors=[],
    )


def one(ing, **kw):
    return estimate_probabilities(request([ing], **kw)).probabilities[0]


def test_confirmed_formula_matches_crawling_prior():  # §7-1, 17
    p = one(ingredient())
    assert (p.alpha_post, p.beta_post) == (54, 5)
    assert p.posterior_mean == pytest.approx(0.915, abs=1e-3)
    assert p.prior_source == "global"


def test_store_prior_adds_alpha_beta_minus_one():  # §7-2, 22
    p = one(ingredient(), store=[(10, 3.0, 2.0)])
    assert p.prior_source == "store"
    assert (p.alpha_post, p.beta_post) == (56, 6)  # weight not applied twice


def test_storeless_scan_ignores_store_prior():  # §7-10-b
    p = one(ingredient(), store=[(10, 3.0, 2.0)], store_id=None)
    assert p.prior_source == "global"
    assert (p.alpha_post, p.beta_post) == (54, 5)


def test_no_evidence_is_uninformative_not_safe():  # §7-4, 11, 24
    p = one(ingredient(obs=()))
    assert p.prior_source == "uninformative"
    assert p.posterior_mean == 0.5
    assert p.confidence == 0


def test_zero_recipes_is_uninformative():  # §7-11
    p = one(ingredient(obs=(("10000recipe", 0, 0),)))
    assert p.prior_source == "uninformative" and p.posterior_mean == 0.5


@pytest.mark.parametrize("source", ["variant_suggested"])
def test_variant_scale_keeps_mean(source):  # §7-5, 5-b
    p = one(ingredient(obs=(("10000recipe", 2, 10),), source=source, basis="direct"))
    assert p.posterior_mean == pytest.approx(0.25)
    assert (p.alpha_post, p.beta_post) == (1.5, 4.5)


def test_db_registered_variant_is_full_scale():  # §7-6
    p = one(ingredient(source="recipe"))
    assert p.alpha_post == 54


def test_depth_decay_shrinks_spread_not_mean():  # §7-7
    d0 = one(ingredient(source="expanded", depth=0, basis="inherited"))
    d3 = one(ingredient(source="expanded", depth=3, basis="inherited"))
    assert d0.posterior_mean == pytest.approx(d3.posterior_mean)
    assert d3.alpha_post == pytest.approx(54 * 0.125)
    assert d3.confidence < d0.confidence


def test_direct_sub_ingredient_is_not_decayed():  # §7-7-b
    p = one(ingredient(source="expanded", depth=2, basis="direct"))
    assert p.alpha_post == 54


def test_anomaly_lock_passes_through():  # §7-9
    p = one(ingredient(locked=True))
    assert p.anomaly_locked is True


def test_same_input_same_output():  # §7-13
    req = request([ingredient()])
    assert estimate_probabilities(req) == estimate_probabilities(req)


def test_web_ingredient_uses_web_reliability_without_extra_scale():  # §7-14, 27
    p = one(
        ingredient(source="web_search", obs=(("10000recipe", 6, 10),)),
        weights=(("web_search", 0.6), ("corpus:10000recipe", 1.0)),
    )
    assert (p.alpha_post, p.beta_post) == pytest.approx((1 + 3.6, 1 + 2.4))


def test_web_ingredient_without_observations_is_uninformative():  # §7-14-b
    p = one(ingredient(source="web_search", obs=()))
    assert p.prior_source == "uninformative"


def test_low_reliability_moves_toward_half_not_zero():  # §7-18
    full = one(ingredient(obs=(("10000recipe", 5, 50),)))
    weak = one(ingredient(obs=(("10000recipe", 5, 50),)), weights=(("corpus:10000recipe", 0.2),))
    assert full.posterior_mean < weak.posterior_mean < 0.5
    assert weak.confidence < full.confidence


def test_curated_adds_alpha_only():  # §7-19, 20
    base = one(ingredient())
    curated = one(ingredient(curated=True), weights=(("corpus:10000recipe", 1.0), ("curated", 0.8)))
    assert curated.alpha_post == pytest.approx(base.alpha_post + 0.8)
    assert curated.beta_post == base.beta_post


def test_curated_only_is_global_evidence():
    p = one(ingredient(obs=(), curated=True))
    assert p.prior_source == "global" and p.posterior_mean > 0.5


def test_two_corpora_are_summed_with_own_weights():  # §7-21
    p = one(
        ingredient(obs=(("10000recipe", 8, 10), ("semie", 2, 4))),
        weights=(("corpus:10000recipe", 1.0), ("corpus:semie", 0.5)),
    )
    assert (p.alpha_post, p.beta_post) == pytest.approx((1 + 8 + 1, 1 + 2 + 1))


def test_unknown_source_gets_half_weight():  # §6
    p = one(ingredient(obs=(("wtable", 4, 4),)))
    assert p.alpha_post == pytest.approx(3)


@pytest.mark.parametrize("k", [53, 28])
def test_confidence_ignores_where_the_mean_sits(k):  # §7-25
    p = one(ingredient(obs=(("10000recipe", k, 57),)))
    assert p.confidence == pytest.approx(0.87, abs=0.01)


def test_confidence_low_threshold():  # §7-26
    assert confidence_score(1 + 4.9, 1) < 0.59 <= confidence_score(1 + 5.0, 1) + 1e-9


def test_unavailable_store_slice_is_not_store_evidence():  # §7-29
    p = one(ingredient(), priors_status="unavailable")
    assert p.prior_source == "global"


def test_empty_ingredients_warns():
    res = estimate_probabilities(request([]))
    assert [w.code for w in res.warnings] == ["empty_ingredients"]


def test_tool_keeps_context_and_warnings():  # §7-16
    upstream = NodeWarning(
        code="store_context_missing", node="supervisor", item_id=ITEM_ID,
        message="storeless", forces_caution=True,
    )
    req = request([ingredient()], warnings=[upstream], store_id=None)
    res = asyncio.run(BayesianTool().run(req))
    assert res.context == req.context
    assert [w.code for w in res.warnings] == ["store_context_missing"]
