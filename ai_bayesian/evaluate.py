"""Allergen recall eval on the repo seed (#192 QA).

Truth: the curated recipe (ai_ruleengine/data/menus.csv) plus hidden-ingredient tags
says which allergen tags a menu has. Model: ④→⑤ on crawl statistics only; the curated
recipe is hidden from it so the model cannot read the answer. Score of (menu, tag) is
the highest posterior among ingredients carrying that tag.

    python -m ai_bayesian.evaluate
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_ontology import lookup_tags, query_ontology
from ai_ontology.tests.seed import seed
from ai_ruleengine.constants import ALL_TAGS
from ai_supervisor.contracts.bayesian import BayesianRequest
from ai_supervisor.contracts.ontology import OntologyRequest

from .estimate import estimate_probabilities

THRESHOLDS = (0.01, 0.05, 0.1, 0.2, 0.3, 0.5)
CONTEXT = {
    "schema_version": "3.0",
    "trace_id": "0199a2f0-0000-7000-8000-000000000001",
    "scan_session_id": "eval",
    "store_id": None,
    "call_scope": "item",
    "item_id": "eval:0",
}
FIELDS = ("ingredient", "constraint_tags", "role", "source", "depth", "curated",
          "observation_basis", "observations", "anomaly_locked", "evidence_refs")


@dataclass(frozen=True)
class Pair:
    menu: str
    tag: str
    present: bool
    score: float


@dataclass(frozen=True)
class Point:
    threshold: float
    recall: float
    precision: float
    f2: float
    fn: int
    fp: int


def _truth(s, menu: str) -> set[str]:
    ids = [s.id[i] for i in s.recipe[menu]]
    tags = lookup_tags(ids, s.graph, CONTEXT["item_id"]).tags
    return {t for i in ids for t in tags[i]}


def _scores(s, menu: str) -> dict[str, float] | None:
    knowledge = s.ontology(menu).model_copy(update={"recipe_ingredients": []})
    menu_id = s.menu_id[menu]
    ontology = query_ontology(
        OntologyRequest.model_validate(
            {
                "context": CONTEXT,
                "menu": {
                    "menu_id": menu_id,
                    "normalized_menu_name": menu,
                    "match_candidates": [
                        {"menu_id": menu_id, "menu_category_id": None, "canonical_name": menu,
                         "score": 1.0, "match_type": "canonical"}
                    ],
                    "residual_tokens": [],
                },
                "unconfirmed_only": True,
                "confirmed_ingredients": [],
                "anomaly_locked_ingredients": [],
                "knowledge": knowledge,
                "ingredient_graph": s.graph,
                "warnings": [],
                "errors": [],
            }
        )
    )
    if not ontology.ingredients:
        return None  # no crawl data for this menu
    request = BayesianRequest.model_validate(
        {
            "context": CONTEXT,
            "menu_category_id": None,
            "ingredients": [{f: getattr(i, f) for f in FIELDS} for i in ontology.ingredients],
            "priors": {"status": "not_found", "store": [], "cluster": [], "global": [], "evidence": []},
            "source_reliability": {"status": "not_found", "weights": []},
            "warnings": [],
            "errors": [],
        }
    )
    best: dict[str, float] = {}
    for p in estimate_probabilities(request).probabilities:
        for tag in p.constraint_tags:
            best[tag] = max(best.get(tag, 0.0), p.posterior_mean)
    return best


def pairs() -> tuple[list[Pair], list[str]]:
    s = seed()
    rows: list[Pair] = []
    skipped: list[str] = []
    for menu in sorted(s.recipe):
        scores = _scores(s, menu)
        if scores is None:
            skipped.append(menu)
            continue
        truth = _truth(s, menu)
        rows += [Pair(menu, t, t in truth, scores.get(t, 0.0)) for t in ALL_TAGS]
    return rows, skipped


def at(rows: list[Pair], threshold: float) -> Point:
    positives = sum(r.present for r in rows)
    tp = sum(1 for r in rows if r.present and r.score >= threshold)
    fp = sum(1 for r in rows if not r.present and r.score >= threshold)
    recall = tp / positives if positives else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    f2 = 5 * precision * recall / (4 * precision + recall) if precision + recall else 0.0
    return Point(threshold, recall, precision, f2, positives - tp, fp)


def auc(rows: list[Pair]) -> float:
    pos = [r.score for r in rows if r.present]
    neg = [r.score for r in rows if not r.present]
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def main() -> None:
    rows, skipped = pairs()
    menus = len({r.menu for r in rows})
    print(f"menus {menus} (no crawl data: {len(skipped)} {skipped}), pairs {len(rows)}, "
          f"positives {sum(r.present for r in rows)}")
    print("threshold  recall  precision  F2     FN   FP")
    for th in THRESHOLDS:
        p = at(rows, th)
        print(f"{th:<9}  {p.recall:.3f}   {p.precision:.3f}      {p.f2:.3f}  {p.fn:<4} {p.fp}")
    print(f"AUC {auc(rows):.3f}")
    missed = [(r.menu, r.tag) for r in rows if r.present and r.score == 0.0]
    print(f"never flagged ({len(missed)}): {missed}")


if __name__ == "__main__":
    main()
