"""Knowledge built from the repo seed sources, the same rules as the DB seed export.

Sources (crawling/README.md "DB 시드 재생성"):
- curated recipes: ai_ruleengine/data/menus.csv
- corpus counts: crawling/<site>_prior.csv
- tags: ai_ruleengine/constants.py VARIANT_INGREDIENTS (longest keyword first, substring)
- part-of edges and path tags: ai_result/rules/hidden_rules_data.py
- taxonomy: crawling/ingredient_taxonomy.csv

Test helper only. The backend builds the real KnowledgeBundle from the DB.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from ai_result.rules.hidden_rules_data import HIDDEN_RULES
from ai_ruleengine.constants import VARIANT_INGREDIENTS
from ai_supervisor.contracts.external import IngredientGraph, OntologySlice
from crawling.normalize_ingredients import CATEGORY_RULES, EXACT_MAP, canonicalize, clean_text, is_tool_leak

ROOT = Path(__file__).resolve().parents[2]
SITES = ("semie", "wtable", "10000recipe")


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _canon(text: str) -> str | None:
    cleaned = clean_text(text)
    if not cleaned or is_tool_leak(cleaned):
        return None
    return canonicalize(cleaned)[0]


class Seed:
    def __init__(self) -> None:
        known = {c for c, _ in CATEGORY_RULES} | set(EXACT_MAP.values())
        self.priors = {s: _rows(ROOT / f"crawling/{s}_prior.csv") for s in SITES}
        curated = _rows(ROOT / "ai_ruleengine/data/menus.csv")
        self.recipe: dict[str, list[str]] = {}
        self.flags: dict[str, list[str]] = {}
        categories = sorted({r["카테고리"].strip() for r in curated})
        category_id = {c: i + 1 for i, c in enumerate(categories)}
        self.menu_category: dict[str, int] = {}
        for r in curated:
            name = r["메뉴명"].strip()
            items = [_canon(x.strip()) for x in r["레시피"].split(",")]
            self.recipe[name] = list(dict.fromkeys(x for x in items if x))
            self.flags[name] = sorted({f.strip() for f in r["애매함 태그"].split(",") if f.strip()})
            self.menu_category[name] = category_id[r["카테고리"].strip()]

        self.edges: dict[tuple[str, str], str] = {}
        self.edge_tags: set[tuple[str, str, str]] = set()
        for parent, flags in HIDDEN_RULES.items():
            p = _canon(parent)
            for flag, children in flags.items():
                for child in children:
                    c = _canon(child["name"])
                    if not p or not c or p == c:
                        continue
                    self.edges.setdefault((p, c), flag)
                    self.edge_tags.add((p, c, child["tag"]))

        names = {r["ingredient_canonical"] for rows in self.priors.values() for r in rows}
        names = {n for n in names if n in known}
        names |= {i for items in self.recipe.values() for i in items}
        names |= {x for edge in self.edges for x in edge}
        self.ingredients = sorted(names)
        self.id = {name: i + 1 for i, name in enumerate(self.ingredients)}
        menus = sorted({r["menu_name"] for rows in self.priors.values() for r in rows} | set(self.recipe))
        self.menu_id = {name: i + 1 for i, name in enumerate(menus)}
        taxonomy = {r["name_ko"]: r["taxonomy_category"] for r in _rows(ROOT / "crawling/ingredient_taxonomy.csv")}
        keywords = {t: sorted(k, key=len, reverse=True) for t, k in VARIANT_INGREDIENTS.items()}

        self.graph = IngredientGraph.model_validate(
            {
                "status": "ok",
                "ingredients": [
                    {
                        "ingredient_id": self.id[n],
                        "canonical_name": n,
                        "name_normalized": n,
                        "localized_name": None,
                        "taxonomy_category": taxonomy.get(n) or "unknown",
                    }
                    for n in self.ingredients
                ],
                "aliases": [],  # the seed has no alias rows
                "tags": [
                    {"ingredient_id": self.id[n], "tag_code": t}
                    for n in self.ingredients
                    for t, ks in keywords.items()
                    if any(k in n for k in ks)
                ],
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
