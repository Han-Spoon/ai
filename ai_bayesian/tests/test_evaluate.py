"""Regression floor for the seed allergen eval (python -m ai_bayesian.evaluate)."""

from ai_bayesian.evaluate import at, auc, pairs


def test_seed_allergen_eval_does_not_regress():
    rows, _ = pairs()
    # ⑧ flags any tagged ingredient, so the lowest threshold is the service recall
    assert at(rows, 0.01).recall >= 0.98
    assert at(rows, 0.1).f2 >= 0.75
    assert auc(rows) >= 0.9


def test_eval_hides_the_answer_from_the_model():
    rows, _ = pairs()
    assert all(0.0 <= r.score < 1.0 for r in rows)
