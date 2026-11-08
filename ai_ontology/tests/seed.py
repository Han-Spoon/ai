"""Knowledge built from the repo seed sources with the DB seed export rules.

The rules live in ``crawling/db_seed.py`` (the script that writes the CSVs the backend
loads). This helper turns its output into the bundle shapes ④⑤ read. Test helper only:
the backend builds the real KnowledgeBundle from the DB.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from ai_supervisor.contracts.external import IngredientGraph, OntologySlice

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "crawling"))

from db_seed import SITES, build, canon  # noqa: E402

_canon = canon  # kept for older call sites


class Seed:
    def __init__(self) -> None:
        d = build()
        self.data = d
        self.priors = d.priors
        self.recipe = d.recipe
        self.flags = d.menu_flags
        category_id = {c: i + 1 for i, c in enumerate(d.categories)}
        self.menu_category = {m: category_id[c] for m, c in d.menu_category.items()}
        self.edges = d.edges
        self.edge_tags = set(d.edge_tags)
        self.ingredients = d.ingredients
        self.id = d.ingredient_id
        self.menu_id = d.menu_id
        self.graph = IngredientGraph.model_validate(
            {
                "status": "ok",
                "ingredients": [
                    {
                        "ingredient_id": self.id[n],
                        "canonical_name": n,
                        "name_normalized": n,
                        "localized_name": None,
                        "taxonomy_category": d.taxonomy[n],
                    }
                    for n in self.ingredients
                ],
                "aliases": [],  # the seed has no alias rows
                "tags": [{"ingredient_id": self.id[n], "tag_code": t} for n in self.ingredients for t in d.tags[n]],
                "compositions": [
                    {"parent_ingredient_id": self.id[p], "child_ingredient_id": self.id[c], "ambiguity_flag": f}
                    for (p, c), f in self.edges.items()
                ],
                "composition_tags": [
                    {"parent_ingredient_id": self.id[p], "child_ingredient_id": self.id[c], "tag_code": t}
                    for p, c, t in sorted(self.edge_tags)
                ],
            }
        )

    def ontology(self, menu: str) -> OntologySlice:
        corpora, priors = [], []
        for site, rows in self.priors.items():
            menu_rows = [r for r in rows if r["menu_name"] == menu]
            if not menu_rows:
                continue
            corpora.append({"corpus": site, "n_total": int(menu_rows[0]["n_total"])})
            priors += [
                {"corpus": site, "ingredient_id": self.id[r["ingredient_canonical"]], "k_count": int(r["k_count"])}
                for r in menu_rows
                if r["ingredient_canonical"] in self.id
            ]
        return OntologySlice.model_validate(
            {
                "status": "ok",
                "menu": {
                    "menu_id": self.menu_id[menu],
                    "base_menu_id": None,
                    "remain_token": None,
                    "menu_category_id": self.menu_category.get(menu),
                    "source": "curated",
                    "ambiguity_flags": self.flags.get(menu, []),
                    "knowledge_revision": 1,
                },
                "recipe_ingredients": [{"ingredient_id": self.id[i]} for i in self.recipe.get(menu, [])],
                "menu_recipe_corpora": corpora,
                "menu_ingredient_priors": priors,
                "cache": None,
            }
        )


@lru_cache(maxsize=1)
def seed() -> Seed:
    return Seed()
