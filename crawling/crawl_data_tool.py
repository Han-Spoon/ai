# -*- coding: utf-8 -*-
"""Validate and stage recipe crawl CSVs without mutating committed source data."""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

try:
    from .filter_titles_additional_25 import keeps
except ImportError:  # python crawling/crawl_data_tool.py
    from filter_titles_additional_25 import keeps


FIELDS = ["menu_query", "recipe_title", "recipe_url", "ingredient_name"]


def read_menus(path: Path) -> list[str]:
    menus = [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    duplicates = sorted(menu for menu, count in Counter(menus).items() if count > 1)
    if duplicates:
        raise ValueError(f"menu file contains duplicates: {', '.join(duplicates)}")
    if not menus:
        raise ValueError(f"menu file is empty: {path}")
    return menus


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise ValueError(f"{path}: expected columns {FIELDS}, got {reader.fieldnames}")
        return [{field: (row.get(field) or "").strip() for field in FIELDS} for row in reader]


def validate(args: argparse.Namespace) -> int:
    expected = read_menus(Path(args.menu_file))
    expected_set = set(expected)
    rows = read_rows(Path(args.input_csv))
    errors: list[str] = []
    warnings: list[str] = []

    blank_rows = [index for index, row in enumerate(rows, start=2) if any(not row[field] for field in FIELDS)]
    if blank_rows:
        errors.append(f"blank required fields at rows: {blank_rows[:20]}")

    actual_set = {row["menu_query"] for row in rows if row["menu_query"]}
    missing = [menu for menu in expected if menu not in actual_set]
    unexpected = sorted(actual_set - expected_set)
    if missing:
        message = f"menus with no ingredient rows: {', '.join(missing)}"
        if args.require_all_menus:
            errors.append(message)
        else:
            warnings.append(message)
    if unexpected:
        errors.append(f"unexpected menus: {', '.join(unexpected)}")

    exact_counts = Counter(tuple(row[field] for field in FIELDS) for row in rows)
    duplicate_rows = sum(count - 1 for count in exact_counts.values() if count > 1)
    if duplicate_rows:
        warnings.append(f"duplicate exact rows: {duplicate_rows} (staging merge removes them)")

    url_menus: dict[str, set[str]] = defaultdict(set)
    recipes_by_menu: dict[str, set[str]] = defaultdict(set)
    rows_by_menu: Counter[str] = Counter()
    bad_titles: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        menu = row["menu_query"]
        url = row["recipe_url"]
        url_menus[url].add(menu)
        recipes_by_menu[menu].add(url)
        rows_by_menu[menu] += 1
        if args.check_titles and not keeps(menu, row["recipe_title"]):
            bad_titles[menu].add(row["recipe_title"])

    cross_menu_urls = {url: menus for url, menus in url_menus.items() if len(menus) > 1}
    if cross_menu_urls:
        message = f"recipe URLs assigned to multiple menus: {len(cross_menu_urls)}"
        if args.fail_cross_menu_urls:
            errors.append(message)
        else:
            warnings.append(message)

    if bad_titles:
        for menu, titles in sorted(bad_titles.items()):
            examples = " | ".join(sorted(titles)[:3])
            errors.append(f"title filter mismatch {menu}: {len(titles)} recipes ({examples})")

    print("menu_query,recipe_count,ingredient_row_count")
    for menu in expected:
        recipe_count = len(recipes_by_menu[menu])
        print(f"{menu},{recipe_count},{rows_by_menu[menu]}")
        if 0 < recipe_count < args.low_recipe_threshold:
            warnings.append(
                f"{menu}: only {recipe_count} recipes (< {args.low_recipe_threshold})"
            )

    for warning in warnings:
        print(f"[WARN] {warning}")
    for error in errors:
        print(f"[ERROR] {error}")
    return 1 if errors else 0


def merge(args: argparse.Namespace) -> int:
    expected = read_menus(Path(args.menu_file))
    expected_set = set(expected)
    base_path = Path(args.base)
    additional_path = Path(args.additional)
    out_path = Path(args.out)
    if out_path.resolve() in {base_path.resolve(), additional_path.resolve()}:
        raise ValueError("output must differ from both inputs")
    if out_path.exists() and not args.force:
        raise FileExistsError(f"output already exists (use --force to replace): {out_path}")

    base_rows = read_rows(base_path)
    additional_rows = read_rows(additional_path)
    overlap = sorted({row["menu_query"] for row in base_rows} & expected_set)
    if overlap:
        raise ValueError(f"base already contains additional menu queries: {', '.join(overlap)}")
    actual_additional = {row["menu_query"] for row in additional_rows}
    unexpected = sorted(actual_additional - expected_set)
    if unexpected:
        raise ValueError(f"unexpected additional menus: {', '.join(unexpected)}")

    combined: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    duplicate_count = 0
    for row in [*base_rows, *additional_rows]:
        key = tuple(row[field] for field in FIELDS)
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        combined.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(combined)
    print(
        f"base {len(base_rows)} rows + additional {len(additional_rows)} rows "
        f"-> {len(combined)} rows ({duplicate_count} exact duplicates removed): {out_path}"
    )
    return 0


def merge_menus(args: argparse.Namespace) -> int:
    base = read_menus(Path(args.base))
    additional = read_menus(Path(args.additional))
    overlap = sorted(set(base) & set(additional))
    if overlap:
        raise ValueError(f"menu lists overlap: {', '.join(overlap)}")
    out_path = Path(args.out)
    if out_path.resolve() in {Path(args.base).resolve(), Path(args.additional).resolve()}:
        raise ValueError("output must differ from both inputs")
    if out_path.exists() and not args.force:
        raise FileExistsError(f"output already exists (use --force to replace): {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join([*base, *additional]) + "\n", encoding="utf-8")
    print(f"menus {len(base)} + {len(additional)} -> {len(base) + len(additional)}: {out_path}")
    return 0


def combine(args: argparse.Namespace) -> int:
    input_paths = [Path(value) for value in args.inputs]
    out_path = Path(args.out)
    if out_path.resolve() in {path.resolve() for path in input_paths}:
        raise ValueError("output must differ from every input")
    if out_path.exists() and not args.force:
        raise FileExistsError(f"output already exists (use --force to replace): {out_path}")
    combined: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    duplicate_count = 0
    for path in input_paths:
        for row in read_rows(path):
            key = tuple(row[field] for field in FIELDS)
            if key in seen:
                duplicate_count += 1
                continue
            seen.add(key)
            combined.append(row)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(combined)
    print(f"combined {len(input_paths)} files -> {len(combined)} rows ({duplicate_count} duplicates removed): {out_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate a new crawl CSV")
    validate_parser.add_argument("input_csv")
    validate_parser.add_argument("--menu-file", required=True)
    validate_parser.add_argument("--check-titles", action="store_true")
    validate_parser.add_argument(
        "--require-all-menus",
        action="store_true",
        help="fail when any menu has no rows (use for the combined three-site result)",
    )
    validate_parser.add_argument("--fail-cross-menu-urls", action="store_true")
    validate_parser.add_argument("--low-recipe-threshold", type=int, default=10)
    validate_parser.set_defaults(handler=validate)

    merge_parser = subparsers.add_parser("merge", help="write a staged base + additional CSV")
    merge_parser.add_argument("--base", required=True)
    merge_parser.add_argument("--additional", required=True)
    merge_parser.add_argument("--menu-file", required=True)
    merge_parser.add_argument("--out", required=True)
    merge_parser.add_argument("--force", action="store_true")
    merge_parser.set_defaults(handler=merge)

    menu_parser = subparsers.add_parser("merge-menus", help="write a staged combined menu list")
    menu_parser.add_argument("--base", required=True)
    menu_parser.add_argument("--additional", required=True)
    menu_parser.add_argument("--out", required=True)
    menu_parser.add_argument("--force", action="store_true")
    menu_parser.set_defaults(handler=merge_menus)

    combine_parser = subparsers.add_parser("combine", help="combine crawl CSVs for cross-site validation")
    combine_parser.add_argument("inputs", nargs="+")
    combine_parser.add_argument("--out", required=True)
    combine_parser.add_argument("--force", action="store_true")
    combine_parser.set_defaults(handler=combine)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
