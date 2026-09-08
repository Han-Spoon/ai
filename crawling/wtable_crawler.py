# -*- coding: utf-8 -*-
"""
우리의식탁(wtable.co.kr) 레시피 재료 크롤러.

출력 CSV 컬럼:
    menu_query, recipe_title, recipe_url, ingredient_name

재료 수량(value)은 저장하지 않고, 조리도구/도구 섹션은 제외합니다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests


WTABLE_API_BASE = "https://wtable.net"
WTABLE_WEB_BASE = "https://wtable.co.kr"

DEFAULT_MENU_FILE = Path(__file__).with_name("menu_queries.txt")
DEFAULT_OUT = Path(__file__).with_name("wtable_recipes_raw.csv")
DEFAULT_REPORT = Path(__file__).with_name("wtable_crawl_report.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "X-App-Platform": "web",
    "X-App-Version": "1",
}

TOOL_KEYWORDS = {
    "냄비",
    "도마",
    "나이프",
    "요리스푼",
    "숟가락",
    "젓가락",
    "국자",
    "가위",
    "채반",
    "쟁반",
    "밧드",
    "믹싱볼",
    "장갑",
    "트레이",
    "볶음팬",
    "프라이팬",
    "후라이팬",
    "궁중팬",
    "계량컵",
    "계량스푼",
    "뚝배기",
    "접시",
    "그릇",
    "오븐",
    "에어프라이어",
    "전자레인지",
}

TOOL_GROUP_HINTS = {"도구", "조리도구", "준비도구", "필요도구", "키친가이드", "장비"}


class NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_next_data = False
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self._in_next_data = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_next_data:
            self._in_next_data = False

    def handle_data(self, data: str) -> None:
        if self._in_next_data:
            self._chunks.append(data)

    @property
    def data(self) -> dict[str, Any]:
        if not self._chunks:
            raise ValueError("__NEXT_DATA__ script not found")
        return json.loads("".join(self._chunks))


@dataclass(frozen=True)
class RecipeSummary:
    title: str
    token: str

    @property
    def url(self) -> str:
        return f"{WTABLE_WEB_BASE}/recipes/{self.token}"


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def is_tool_text(value: str) -> bool:
    compact = compact_text(value)
    return any(keyword in compact for keyword in TOOL_KEYWORDS)


def is_tool_group(group_name: str) -> bool:
    compact = compact_text(group_name)
    return any(hint in compact for hint in TOOL_GROUP_HINTS)


def exact_or_space_insensitive_match(menu: str, title: str) -> bool:
    return compact_text(menu) == compact_text(title)


def request_json(session: requests.Session, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def search_recipes(session: requests.Session, menu: str, *, exact_title_only: bool) -> list[RecipeSummary]:
    payload = request_json(
        session,
        f"{WTABLE_API_BASE}/api_v2/recipe/search",
        params={"q": menu},
    )
    items = payload.get("data") or []
    results: list[RecipeSummary] = []
    seen: set[str] = set()
    for item in items:
        title = (item.get("title") or "").strip()
        token = (item.get("token") or "").strip()
        if not title or not token or token in seen:
            continue
        if exact_title_only and not exact_or_space_insensitive_match(menu, title):
            continue
        seen.add(token)
        results.append(RecipeSummary(title=title, token=token))
    return results


def extract_recipe_from_html(html: str) -> dict[str, Any]:
    parser = NextDataParser()
    parser.feed(html)
    page_props = parser.data.get("props", {}).get("pageProps", {})
    recipe = page_props.get("recipe")
    if not isinstance(recipe, dict):
        raise ValueError("recipe object not found in __NEXT_DATA__")
    return recipe


def fetch_recipe_detail(session: requests.Session, token: str) -> dict[str, Any]:
    api_url = f"{WTABLE_API_BASE}/api_v2/recipe/view/{token}"
    try:
        payload = request_json(session, api_url)
        recipe = payload.get("data")
        if isinstance(recipe, dict) and recipe.get("recipe_igroups") is not None:
            return recipe
    except Exception as exc:
        print(f"[WARN] detail API failed for {token}: {exc}", file=sys.stderr)

    web_url = f"{WTABLE_WEB_BASE}/recipes/{token}"
    response = session.get(web_url, timeout=20)
    response.raise_for_status()
    return extract_recipe_from_html(response.text)


def iter_ingredient_names(recipe: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for group in recipe.get("recipe_igroups") or []:
        if not isinstance(group, dict):
            continue
        if is_tool_group(str(group.get("name") or "")):
            continue
        for ingredient in group.get("ingredients") or []:
            if not isinstance(ingredient, dict):
                continue
            name = (ingredient.get("name") or "").strip()
            if not name or is_tool_text(name):
                continue
            names.append(name)
    return names


def read_menu_queries(path: Path) -> list[str]:
    menus: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        menus.append(line)
    return menus


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def crawl(args: argparse.Namespace) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    menus = read_menu_queries(Path(args.menu_file))
    if not menus:
        raise ValueError(f"menu file is empty: {args.menu_file}")

    session = requests.Session()
    session.headers.update(HEADERS)

    data_rows: list[dict[str, str]] = []
    report_rows: list[dict[str, str]] = []

    for index, menu in enumerate(menus, start=1):
        try:
            recipes = search_recipes(session, menu, exact_title_only=args.exact_title_only)
            if args.max_recipes_per_menu:
                recipes = recipes[: args.max_recipes_per_menu]

            ingredient_row_count = 0
            for recipe_summary in recipes:
                detail = fetch_recipe_detail(session, recipe_summary.token)
                title = (detail.get("title") or recipe_summary.title).strip()
                url = f"{WTABLE_WEB_BASE}/recipes/{recipe_summary.token}"
                ingredients = iter_ingredient_names(detail)
                ingredient_row_count += len(ingredients)
                for ingredient_name in ingredients:
                    data_rows.append(
                        {
                            "menu_query": menu,
                            "recipe_title": title,
                            "recipe_url": url,
                            "ingredient_name": ingredient_name,
                        }
                    )
                time.sleep(args.delay)

            report_rows.append(
                {
                    "menu_query": menu,
                    "recipe_count": str(len(recipes)),
                    "ingredient_row_count": str(ingredient_row_count),
                    "status": "ok" if recipes else "no_recipe",
                }
            )
            print(f"[{index:02d}/{len(menus)}] {menu}: {len(recipes)} recipes, {ingredient_row_count} ingredients")
        except Exception as exc:
            report_rows.append(
                {
                    "menu_query": menu,
                    "recipe_count": "0",
                    "ingredient_row_count": "0",
                    "status": f"error: {exc}",
                }
            )
            print(f"[ERROR] {menu}: {exc}", file=sys.stderr)
        time.sleep(args.delay)

    return data_rows, report_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl Wtable recipe ingredients.")
    parser.add_argument("--menu-file", default=str(DEFAULT_MENU_FILE), help="메뉴명 목록 txt 파일")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="크롤링 원본 CSV 출력 경로")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="메뉴별 수집 현황 CSV 출력 경로")
    parser.add_argument("--max-recipes-per-menu", type=int, default=0, help="메뉴당 최대 레시피 수(0=전체)")
    parser.add_argument("--delay", type=float, default=0.15, help="요청 사이 대기 초")
    parser.add_argument("--exact-title-only", action="store_true", help="레시피 제목이 메뉴명과 정확히 같은 경우만 수집")
    args = parser.parse_args()
    return args


def main() -> None:
    args = parse_args()
    rows, report = crawl(args)
    write_csv(Path(args.out), rows, ["menu_query", "recipe_title", "recipe_url", "ingredient_name"])
    write_csv(Path(args.report), report, ["menu_query", "recipe_count", "ingredient_row_count", "status"])
    print(f"\n완료: {args.out}")
    print(f"수집 행 수: {len(rows)}")
    print(f"리포트: {args.report}")


if __name__ == "__main__":
    main()
