"""Build the AI knowledge DB seed from repo sources (crawling/README.md "DB 시드 재생성").

    python crawling/db_seed.py --out <dir>      # writes one CSV per table
    python crawling/db_seed.py --register-ids   # adds ids for new menus/ingredients

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
from normalize_ingredients import (  # noqa: E402
    CATEGORY_RULES,
    EXACT_MAP,
    canonicalize,
    clean_text,
    compact_name,
    is_tool_leak,
)

SITES = ("semie", "wtable", "10000recipe")
RAW_TAXONOMY = {"base", "garnish"}

# Tag fixes on top of the keyword dictionary (ai_ruleengine/constants.py), which tags by
# substring and so misfires on names that only contain a keyword. Reviewed by hand
# against all 324 seed ingredients (2026-11; 돼지감자 added 2026-11-22 with the 22 Yongsan
# menus, 325 now). Removals are only cases that are certainly
# wrong; anything doubtful stays tagged (FN first).
TAG_REMOVE: dict[str, set[str]] = {
    "강낭콩": {"is_soybean"},  # kidney bean, not soybean (콩)
    "렌틸콩": {"is_soybean"},
    "병아리콩": {"is_soybean"},
    "완두콩": {"is_soybean"},
    "땅콩": {"is_soybean"},  # peanut keeps is_peanut
    "둥굴레": {"is_shellfish"},  # a herb tea root (굴)
    "굴비": {"is_shellfish"},  # dried yellow croaker, a fish (굴)
    "멍게": {"is_crab"},  # sea squirt (게)
    "성게": {"is_crab"},  # sea urchin (게)
    "밀크초콜릿": {"is_wheat"},  # 밀크 = milk (밀)
    "햄버거번": {"is_pork"},  # a bread bun (햄)
    "돼지감자": {"is_pork"},  # Jerusalem artichoke, a root vegetable (돼지)
}
# Recipe bundles that are another dish. wtable's "간장게장" search returned 911 soy sauce
# recipes (3 with crab); its "약과" bundle is one ice-cream recipe.
CORPUS_DROP = {("wtable", "간장게장"), ("wtable", "약과")}
TAG_ADD: dict[str, set[str]] = {
    "햄버거번": {"is_wheat"},
    "다시다": {"is_beef"},  # beef stock powder
    "카레가루": {"is_wheat"},  # curry powder usually has wheat flour
    "베이크드빈스": {"is_tomato"},  # beans in tomato sauce
    "젓갈": {"is_shrimp"},  # generic jeotgal can be 새우젓
    "곱창": {"is_pork"},  # 돼지곱창 (순대곱창) is common
    "와인": {"is_sulfite"},  # wine almost always has sulfites
}


# Fixed ids (kind, name_ko, id). Ids used to be sorted-name positions, so one new name
# shifted every id after it (#393: 68 menu ids, 248 ingredient ids), and the backend's
# stored references (store menu links, owner answers, caches) would point at other rows.
# Rows here never change or get reused; a new name takes the next id after the largest
# (``register_new_ids``). Base: the seed before #393, as first loaded.
SEED_IDS = ROOT / "crawling/seed_ids.csv"
ID_KINDS = ("menu_category", "menu", "ingredient")


def load_ids(path: Path = SEED_IDS) -> dict[str, dict[str, int]]:
    ids: dict[str, dict[str, int]] = {k: {} for k in ID_KINDS}
    if path.is_file():
        for r in _rows(path):
            ids[r["kind"]][r["name_ko"]] = int(r["id"])
    return ids


def _assign(names: list[str], fixed: dict[str, int]) -> dict[str, int]:
    """Fixed id for known names; new names (sorted) get ids after the largest ever used."""

    ids = {n: fixed[n] for n in names if n in fixed}
    nxt = max(fixed.values(), default=0) + 1
    for n in sorted(set(names) - set(ids)):
        ids[n] = nxt
        nxt += 1
    return ids


def register_new_ids(path: Path = SEED_IDS) -> list[tuple[str, str, int]]:
    """Append ids for names the seed has but the file lacks. Run it in the PR that adds them."""

    d = build()
    fixed = load_ids(path)
    current = {"menu_category": d.category_id, "menu": d.menu_id, "ingredient": d.ingredient_id}
    new = [(k, n, i) for k in ID_KINDS for n, i in sorted(current[k].items(), key=lambda t: t[1]) if n not in fixed[k]]
    if new:
        write_header = not path.is_file()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["kind", "name_ko", "id"])
            w.writerows(new)
    return new


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
    category_id: dict[str, int]
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


def _owned_path_tags(edge_tags: list[tuple[str, str, str]], tags: dict[str, list[str]]) -> list[tuple[str, str, str]]:
    """Put a path tag only on the child that has the allergen itself.

    The hidden rules tag every child of a parent: 햄 -> 소금 (is_pork), 만두 -> 부추
    (is_pork), 육수 -> 다시마 (is_beef), so ④ asked about salt or kelp. When another child
    of the same parent owns the tag (햄 -> 돼지고기), the parent keeps it through that
    child and the others drop it. With no owning child the tags stay (no FN).
    """

    owners = {(p, t) for p, c, t in edge_tags if t in tags[c]}
    return [(p, c, t) for p, c, t in edge_tags if (p, t) not in owners or t in tags[c]]


def build() -> SeedData:
    known = {c for c, _ in CATEGORY_RULES} | set(EXACT_MAP.values())
    raw_priors = {s: _rows(ROOT / f"crawling/{s}_prior.csv") for s in SITES}
    # ingredient ids are positions in the sorted name list: list names from every bundle,
    # dropped ones too, so a dropped bundle never shifts the ids the backend stored
    priors = {
        s: [
            r
            for r in rows
            if (s, r["menu_name"]) not in CORPUS_DROP
            and compact_name(r["menu_name"]) != compact_name(r["ingredient_canonical"])
        ]
        for s, rows in raw_priors.items()
    }
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

    names = {r["ingredient_canonical"] for rows in raw_priors.values() for r in rows}
    names = {n for n in names if n in known}
    # Keep the reviewed master stable even when recipe filtering removes the last
    # observation of an ingredient. Ingredient ids are sorted-name positions and must
    # not shift merely because an irrelevant recipe bundle was cleaned up.
    names |= set(taxonomy_rows)
    names |= {i for items in recipe.values() for i in items}
    names |= endpoints
    ingredients = sorted(names)
    keywords = {t: sorted(k, key=len, reverse=True) for t, k in VARIANT_INGREDIENTS.items()}
    tags = {
        n: sorted(
            ({t for t, ks in keywords.items() if any(k in n for k in ks)} - TAG_REMOVE.get(n, set()))
            | TAG_ADD.get(n, set())
        )
        for n in ingredients
    }
    menus = sorted({r["menu_name"] for rows in priors.values() for r in rows} | set(recipe))
    fixed = load_ids()
    categories = sorted(set(menu_category.values()))
    return SeedData(
        menus=menus,
        menu_id=_assign(menus, fixed["menu"]),
        menu_category=menu_category,
        categories=categories,
        category_id=_assign(categories, fixed["menu_category"]),
        menu_flags=menu_flags,
        recipe=recipe,
        ingredients=ingredients,
        ingredient_id=_assign(ingredients, fixed["ingredient"]),
        taxonomy={n: taxonomy_rows.get(n) or "unknown" for n in ingredients},
        tags=tags,
        edges=edges,
        edge_tags=_owned_path_tags(list(edge_tags), tags),
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
    cid = d.category_id
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
    parser.add_argument("--out", help="output directory")
    parser.add_argument("--register-ids", action="store_true", help="append ids for new names to seed_ids.csv")
    args = parser.parse_args()
    if args.register_ids:
        for kind, name, i in register_new_ids():
            print(f"{kind} {name} -> {i}")
    if args.out:
        export(Path(args.out))
