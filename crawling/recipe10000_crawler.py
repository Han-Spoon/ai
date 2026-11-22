# -*- coding: utf-8 -*-
"""
만개의레시피(10000recipe.com) 레시피 재료 크롤러.

출력 CSV 컬럼 (다른 사이트 크롤러와 같음):
    menu_query, recipe_title, recipe_url, ingredient_name

검색 결과 목록 순서대로 메뉴당 `--max-recipes-per-menu`개(기본 60개, 기존
`10000recipe_recipes_raw.csv`와 같은 수)를 읽는다. 재료명은 본문의 재료 목록
(`ingre_list_name`)에서 가져오고 수량은 저장하지 않는다. 재료 목록이 없는 옛
페이지는 schema.org Recipe 데이터(`recipeIngredient`)에서 수량을 떼고 쓴다.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests


BASE = "https://www.10000recipe.com"

DEFAULT_MENU_FILE = Path(__file__).with_name("menu_queries.txt")
DEFAULT_OUT = Path(__file__).with_name("10000recipe_recipes_new_raw.csv")
DEFAULT_REPORT = Path(__file__).with_name("10000recipe_crawl_report.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}

_RECIPE_LINK = re.compile(r'href="/recipe/(\d+)"')
_INGRE_NAME = re.compile(r'<div class="ingre_list_name">(.*?)</div>', re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_TOOLS_HEADING = "<b>조리도구</b>"
_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')
_JSON_LD = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
# "돼지고기 600g", "소금 약간", "양파 1/2개" → 마지막 수량 토큰을 뗀다
_QUANTITY_TAIL = re.compile(
    r"\s+(?:[\d./~½¼¾]+\S*|약간|적당량|조금|少|한줌|한꼬집|취향껏|기호에\s*따라)\s*$"
)


RETRY_WAITS = (5, 20, 60)


def get(session: requests.Session, url: str) -> requests.Response:
    """GET with retries: the site drops connections when read for a while (2026-11-22)."""

    for wait in (*RETRY_WAITS, None):
        try:
            return session.get(url, timeout=20)
        except requests.RequestException as exc:
            if wait is None:
                raise
            print(f"  [retry in {wait}s] {url}: {exc}", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def search_recipe_ids(session: requests.Session, menu: str, limit: int, delay: float) -> list[str]:
    ids: list[str] = []
    page = 1
    while len(ids) < limit:
        url = f"{BASE}/recipe/list.html?q={quote(menu)}&page={page}"
        response = get(session, url)
        response.raise_for_status()
        found = list(dict.fromkeys(_RECIPE_LINK.findall(response.text)))
        new = [i for i in found if i not in ids]
        if not new:
            break
        ids.extend(new)
        page += 1
        time.sleep(delay)
    return ids[:limit]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub("", text))).strip()


def _from_json_ld(page: str) -> list[str]:
    for block in _JSON_LD.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("recipeIngredient"):
                names = []
                for line in item["recipeIngredient"]:
                    name = _QUANTITY_TAIL.sub("", str(line).strip()).strip()
                    if name:
                        names.append(name)
                return names
    return []


def parse_recipe(page: str) -> tuple[str, list[str]]:
    title_match = _TITLE.search(page)
    title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip() if title_match else ""
    # 재료 목록 뒤의 "조리도구" 칸(도마·찜기 등)도 같은 ingre_list_name을 쓴다. 그 앞까지만 읽는다.
    tools_at = page.find(_TOOLS_HEADING)
    ingredients_part = page if tools_at < 0 else page[:tools_at]
    names = [n for n in (_clean(raw) for raw in _INGRE_NAME.findall(ingredients_part)) if n]
    if not names:
        names = _from_json_ld(page)
    return title, names


def read_menu_queries(path: Path) -> list[str]:
    menus: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        menus.append(line)
    return menus


def read_listed_ids(path: Path) -> dict[str, list[str]]:
    """메뉴별 레시피 번호를 기존 크롤링 CSV(menu_query, recipe_url)에서 순서대로 읽는다."""

    listed: dict[str, list[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            recipe_id = row["recipe_url"].rstrip("/").rsplit("/", 1)[-1]
            ids = listed.setdefault(row["menu_query"], [])
            if recipe_id not in ids:
                ids.append(recipe_id)
    return listed


DATA_FIELDS = ["menu_query", "recipe_title", "recipe_url", "ingredient_name"]
REPORT_FIELDS = ["menu_query", "recipe_count", "ingredient_row_count", "status"]


def _append_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def _finished_menus(report: Path) -> set[str]:
    if not report.exists():
        return set()
    with report.open(encoding="utf-8-sig", newline="") as f:
        return {r["menu_query"] for r in csv.DictReader(f) if not r["status"].startswith("error")}


def crawl(args: argparse.Namespace) -> tuple[int, int]:
    """메뉴 하나가 끝날 때마다 결과를 파일에 붙인다. 중간에 멈춰도 끝난 메뉴는 남고,
    `--resume`으로 다시 돌리면 리포트에 끝났다고 적힌 메뉴는 건너뛴다."""

    listed = read_listed_ids(Path(args.recipes_from)) if args.recipes_from else None
    menus = list(listed) if listed is not None else read_menu_queries(Path(args.menu_file))
    if not menus:
        raise ValueError(f"menu file is empty: {args.menu_file}")

    out, report = Path(args.out), Path(args.report)
    if args.resume:
        done = _finished_menus(report)
        menus = [m for m in menus if m not in done]
    else:
        out.unlink(missing_ok=True)
        report.unlink(missing_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)
    total_rows = total_recipes = 0

    for index, menu in enumerate(menus, start=1):
        data_rows: list[dict[str, str]] = []
        try:
            if listed is not None:
                ids = listed[menu]
            else:
                ids = search_recipe_ids(session, menu, args.max_recipes_per_menu, args.delay)
            recipe_count = 0
            ingredient_row_count = 0
            for recipe_id in ids:
                url = f"{BASE}/recipe/{recipe_id}"
                try:
                    response = get(session, url)
                except requests.RequestException as exc:
                    print(f"  [skip] {url}: {exc}", file=sys.stderr)
                    continue
                time.sleep(args.delay)
                if response.status_code != 200:
                    continue
                title, names = parse_recipe(response.text)
                if not names:
                    continue
                recipe_count += 1
                ingredient_row_count += len(names)
                for name in names:
                    data_rows.append(
                        {"menu_query": menu, "recipe_title": title, "recipe_url": url, "ingredient_name": name}
                    )
            _append_csv(out, data_rows, DATA_FIELDS)
            _append_csv(
                report,
                [
                    {
                        "menu_query": menu,
                        "recipe_count": str(recipe_count),
                        "ingredient_row_count": str(ingredient_row_count),
                        "status": "ok" if recipe_count else "no_recipe",
                    }
                ],
                REPORT_FIELDS,
            )
            total_rows += len(data_rows)
            total_recipes += recipe_count
            print(f"[{index:02d}/{len(menus)}] {menu}: {recipe_count} recipes, {ingredient_row_count} ingredients", flush=True)
        except Exception as exc:
            # 끝나지 않은 메뉴는 결과를 버리고 오류만 적는다. --resume이 다시 받는다.
            _append_csv(
                report,
                [{"menu_query": menu, "recipe_count": "0", "ingredient_row_count": "0", "status": f"error: {exc}"}],
                REPORT_FIELDS,
            )
            print(f"[ERROR] {menu}: {exc}", file=sys.stderr, flush=True)

    return total_recipes, total_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl 10000recipe ingredients.")
    parser.add_argument("--menu-file", default=str(DEFAULT_MENU_FILE), help="메뉴명 목록 txt 파일")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="크롤링 원본 CSV 출력 경로")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="메뉴별 수집 현황 CSV 출력 경로")
    parser.add_argument("--max-recipes-per-menu", type=int, default=60, help="메뉴당 최대 레시피 수")
    parser.add_argument("--delay", type=float, default=0.3, help="요청 사이 대기 초")
    parser.add_argument(
        "--recipes-from",
        default=None,
        help="검색 대신 이 CSV(menu_query, recipe_url)에 있는 레시피만 다시 읽는다",
    )
    parser.add_argument("--resume", action="store_true", help="리포트에 끝났다고 적힌 메뉴는 건너뛰고 이어서 받는다")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    recipes, rows = crawl(args)
    print(f"\n완료: {args.out}")
    print(f"이번에 받은 레시피 {recipes}개, 재료 행 {rows}개")
    print(f"리포트: {args.report}")


if __name__ == "__main__":
    main()
