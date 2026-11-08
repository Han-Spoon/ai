"""Build the AI knowledge DB seed from repo sources (crawling/README.md "DB 시드 재생성").

    python crawling/db_seed.py --out <dir>      # writes one CSV per table

Same rules as the seed the backend loaded (2026-10), plus one fix (2026-11):
hidden-ingredient rules whose parent is a dish are not composition edges. Seeding maps
each parent to a standard ingredient name, and a dish parent then lands on a raw
ingredient (김밥 → 밥, 열무김치 → 열무, 김칫잎 → 김), putting "fish cake in rice" into
the DB. A rule is dropped when its parent name changes on canonicalization and the new
name is a raw ingredient (taxonomy ``base``/``garnish``). Ingredient ids are unchanged:
every rule endpoint stays in the ingredient master even when its edge is dropped.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "crawling")]

from ai_result.rules.hidden_rules_data import HIDDEN_RULES  # noqa: E402
from ai_ruleengine.constants import ALL_TAGS, VARIANT_INGREDIENTS  # noqa: E402
from normalize_ingredients import CATEGORY_RULES, EXACT_MAP, canonicalize, clean_text, is_tool_leak  # noqa: E402

SITES = ("semie", "wtable", "10000recipe")
RAW_TAXONOMY = {"base", "garnish"}


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def canon(text: str) -> str | None:
    cleaned = clean_text(text)
    if not cleaned or is_tool_leak(cleaned):
        return None
    return canonicalize(cleaned)[0]


@dataclass
class SeedData:
    menus: list[str]
    menu_id: dict[str, int]
    menu_category: dict[str, str]
    categories: list[str]
    menu_flags: dict[str, list[str]]
    recipe: dict[str, list[str]]
    ingredients: list[str]
    ingredient_id: dict[str, int]
    taxonomy: dict[str, str]
    tags: dict[str, list[str]]
    edges: dict[tuple[str, str], str]
    edge_tags: list[tuple[str, str, str]]
    priors: dict[str, list[dict]]
    dropped_rules: list[tuple[str, str]] = field(default_factory=list)  # (rule parent, mapped parent)


def build() -> SeedData:
    known = {c for c, _ in CATEGORY_RULES} | set(EXACT_MAP.values())
    priors = {s: _rows(ROOT / f"crawling/{s}_prior.csv") for s in SITES}
    curated = _rows(ROOT / "ai_ruleengine/data/menus.csv")
    taxonomy_rows = {r["name_ko"]: r["taxonomy_category"] for r in _rows(ROOT / "crawling/ingredient_taxonomy.csv")}

    recipe: dict[str, list[str]] = {}
    menu_category: dict[str, str] = {}
    menu_flags: dict[str, list[str]] = {}
    for r in curated:
        name = r["메뉴명"].strip()
        items = [canon(x.strip()) for x in r["레시피"].split(",")]
        recipe[name] = list(dict.fromkeys(x for x in items if x))
        menu_category[name] = r["카테고리"].strip()
        menu_flags[name] = sorted({f.strip() for f in r["애매함 태그"].split(",") if f.strip()})

    edges: dict[tuple[str, str], str] = {}
    edge_tags: dict[tuple[str, str, str], None] = {}
    endpoints: set[str] = set()
    dropped: list[tuple[str, str]] = []
    for parent, flags in HIDDEN_RULES.items():
        p = canon(parent)
        children = [(flag, canon(ch["name"]), ch["tag"]) for flag, chs in flags.items() for ch in chs]
        endpoints |= {x for x in [p, *(c for _, c, _ in children)] if x}
        if p and p != parent and taxonomy_rows.get(p) in RAW_TAXONOMY:
            dropped.append((parent, p))  # dish parent collapsed onto a raw ingredient
            continue
        for flag, c, tag in children:
            if not p or not c or p == c:
                continue
            edges.setdefault((p, c), flag)
            edge_tags[(p, c, tag)] = None

    names = {r["ingredient_canonical"] for rows in priors.values() for r in rows}
    names = {n for n in names if n in known}
    names |= {i for items in recipe.values() for i in items}
    names |= endpoints
    ingredients = sorted(names)
    keywords = {t: sorted(k, key=len, reverse=True) for t, k in VARIANT_INGREDIENTS.items()}
    menus = sorted({r["menu_name"] for rows in priors.values() for r in rows} | set(recipe))
    return SeedData(
        menus=menus,
        menu_id={m: i + 1 for i, m in enumerate(menus)},
        menu_category=menu_category,
        categories=sorted(set(menu_category.values())),
        menu_flags=menu_flags,
        recipe=recipe,
        ingredients=ingredients,
        ingredient_id={n: i + 1 for i, n in enumerate(ingredients)},
        taxonomy={n: taxonomy_rows.get(n) or "unknown" for n in ingredients},
        tags={n: sorted(t for t, ks in keywords.items() if any(k in n for k in ks)) for n in ingredients},
        edges=edges,
        edge_tags=list(edge_tags),
        priors=priors,
        dropped_rules=dropped,
    )


def _write(out: Path, name: str, fields: list[str], rows: list[dict]) -> None:
    with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"{name}.csv: {len(rows)} rows")


def export(out: Path) -> None:
    d = build()
    out.mkdir(parents=True, exist_ok=True)
    iid, mid = d.ingredient_id, d.menu_id
    cid = {c: i + 1 for i, c in enumerate(d.categories)}
    _write(out, "menu_categories", ["id", "name_ko"], [{"id": cid[c], "name_ko": c} for c in d.categories])
    _write(out, "menus", ["id", "name_ko", "category_id", "base_menu_id", "remain_token", "ambiguity_flags", "source", "needs_review"], [
        {"id": mid[m], "name_ko": m, "category_id": cid.get(d.menu_category.get(m), ""), "base_menu_id": "", "remain_token": "",
         "ambiguity_flags": ("{" + ",".join(d.menu_flags[m]) + "}") if m in d.menu_flags else "",
         "source": "curated", "needs_review": "false" if m in d.menu_category else "true"} for m in d.menus])
    _write(out, "ingredients", ["id", "name_ko", "taxonomy_category"],
           [{"id": iid[g], "name_ko": g, "taxonomy_category": d.taxonomy[g]} for g in d.ingredients])
    _write(out, "ingredient_tags", ["ingredient_id", "tag_code"],
           [{"ingredient_id": iid[g], "tag_code": t} for g in d.ingredients for t in d.tags[g]])
    _write(out, "recipe_ingredients", ["menu_id", "ingredient_id", "evidence_type", "review_item_id"],
           [{"menu_id": mid[m], "ingredient_id": iid[g], "evidence_type": "explicit", "review_item_id": ""}
            for m, items in d.recipe.items() for g in items])
    _write(out, "ingredient_compositions", ["parent_ingredient_id", "child_ingredient_id", "relation_type", "ambiguity_flag"],
           [{"parent_ingredient_id": iid[p], "child_ingredient_id": iid[c], "relation_type": "part_of", "ambiguity_flag": f}
            for (p, c), f in d.edges.items()])
    _write(out, "ingredient_composition_tags", ["parent_ingredient_id", "child_ingredient_id", "tag_code"],
           [{"parent_ingredient_id": iid[p], "child_ingredient_id": iid[c], "tag_code": t} for p, c, t in d.edge_tags])
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    sources = [f"{s}_prior" for s in SITES] + ["menus_csv", "hidden_rules"]
    counts = {f"{s}_prior": len(d.priors[s]) for s in SITES}
    counts["menus_csv"] = sum(len(v) for v in d.recipe.values())
    counts["hidden_rules"] = len(d.edges)
    _write(out, "knowledge_import_batches", ["id", "source", "source_version", "row_count", "status"],
           [{"id": i + 1, "source": s, "source_version": sha, "row_count": counts[s], "status": "completed"}
            for i, s in enumerate(sources)])
    corpora, prior_rows = [], []
    for batch, site in enumerate(SITES, 1):
        n_total: dict[str, int] = {}
        for r in d.priors[site]:
            n_total[r["menu_name"]] = int(r["n_total"])
            if r["ingredient_canonical"] in iid:
                prior_rows.append({"menu_id": mid[r["menu_name"]], "corpus": site, "ingredient_id": iid[r["ingredient_canonical"]],
                                   "n_total": int(r["n_total"]), "k_count": int(r["k_count"])})
        corpora += [{"menu_id": mid[m], "corpus": site, "n_total": n, "import_batch_id": batch} for m, n in sorted(n_total.items())]
    _write(out, "menu_recipe_corpora", ["menu_id", "corpus", "n_total", "import_batch_id"], corpora)
    _write(out, "menu_ingredient_priors", ["menu_id", "corpus", "ingredient_id", "n_total", "k_count"], prior_rows)
    assert all(t in ALL_TAGS for _, _, t in d.edge_tags), "path tag outside the 22 tags"
    print("dropped dish-parent rules:", ", ".join(f"{a}→{b}" for a, b in d.dropped_rules))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export the AI knowledge DB seed CSVs")
    parser.add_argument("--out", required=True, help="output directory")
    export(Path(parser.parse_args().out))
