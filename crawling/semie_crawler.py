# -*- coding: utf-8 -*-
"""
새미네부엌(semie.cooking) 레시피 재료 크롤러.

이 사이트는 자체 검색(/search/a/{query})이 결과를 소수(9개 안팎)로 캡해두고
페이지네이션도 안 먹어서, 메뉴당 15개를 채우기엔 부족하다. 대신 목록 페이지
(cooking/list, recipe-lab/list/recipe)를 끝까지 순회하며 제목만 가볍게
인덱싱한 뒤, 메뉴명이 제목에 포함된 것만 로컬에서 골라 상세페이지를 연다.

유저 게시물(cooking/id/*)은 약 2/3가 재료 필드를 비워둔 채 올라오므로,
상세페이지를 열어 재료가 비어있으면 건너뛰고 다음 후보로 넘어간다.

출력 CSV 컬럼:
    menu_query, recipe_title, recipe_url, ingredient_name

재료명 원문에는 수량이 붙어있어("다진마늘 1스푼 (10g)") 첫 숫자 앞까지만
잘라 이름만 저장한다. 수량 자체는 저장하지 않는다.
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

import requests


BASE = "https://semie.cooking"

DEFAULT_MENU_FILE = Path(__file__).with_name("menu_queries.txt")
DEFAULT_OUT = Path(__file__).with_name("semie_recipes_raw.csv")
DEFAULT_REPORT = Path(__file__).with_name("semie_crawl_report.csv")
DEFAULT_INDEX_CACHE = Path(__file__).with_name("semie_index_cache.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
}

TOOL_KEYWORDS = {
    "냄비", "도마", "나이프", "요리스푼", "숟가락", "젓가락", "국자", "가위",
    "채반", "쟁반", "밧드", "믹싱볼", "장갑", "트레이", "볶음팬", "프라이팬",
    "후라이팬", "궁중팬", "계량컵", "계량스푼", "뚝배기", "접시", "그릇",
    "오븐", "에어프라이어", "전자레인지",
}
TOOL_GROUP_HINTS = {"도구", "조리도구", "준비도구", "필요도구", "장비"}

COOKING_LINK_RE = re.compile(
    r'href="(/cooking/id/\d+)[^"]*"\s+onclick="dataLayerCall\(\'([^\']*)\''
)
RECIPELAB_LINK_RE = re.compile(
    r'href="(/recipe-lab/archive/[a-zA-Z0-9_-]+)[^"]*"\s+'
    r'onclick="dataLayerCall\(\'[^\']*\',\s*\'[^\']*\',\s*\'([^\']*)\''
)


@dataclass(frozen=True)
class IndexEntry:
    title: str
    url: str
    source: str  # "recipe_lab" | "cooking"


class IngredientParser(HTMLParser):
    """<div class="recipe_ingredient"> 블록 안의 (그룹명, 재료원문) 목록을 뽑는다."""

    def __init__(self) -> None:
        super().__init__()
        self.in_block = False
        self.block_div_depth = 0
        self.in_header = False
        self.in_li = False
        self.header_chunks: list[str] = []
        self.li_chunks: list[str] = []
        self.current_group = ""
        self.results: list[tuple[str, str]] = []

    @staticmethod
    def _class_list(attrs: list[tuple[str, str | None]]) -> set[str]:
        cls = dict(attrs).get("class") or ""
        return set(cls.split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.in_block:
            if tag == "div" and "recipe_ingredient" in self._class_list(attrs):
                self.in_block = True
                self.block_div_depth = 1
            return
        if tag == "div":
            self.block_div_depth += 1
        elif tag == "p" and "ingredient_h" in self._class_list(attrs):
            self.in_header = True
            self.header_chunks = []
        elif tag == "li":
            self.in_li = True
            self.li_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if not self.in_block:
            return
        if tag == "div":
            self.block_div_depth -= 1
            if self.block_div_depth == 0:
                self.in_block = False
        elif tag == "p" and self.in_header:
            self.current_group = compact_text("".join(self.header_chunks))
            self.in_header = False
        elif tag == "li" and self.in_li:
            text = " ".join("".join(self.li_chunks).split())
            if text:
                self.results.append((self.current_group, text))
            self.in_li = False

    def handle_data(self, data: str) -> None:
        if self.in_header:
            self.header_chunks.append(data)
        elif self.in_li:
            self.li_chunks.append(data)


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def extract_ingredient_name(raw: str) -> str:
    match = re.search(r"\d", raw)
    name = raw[: match.start()] if match else raw
    name = name.strip(" ()-·/")
    return name.strip()


def is_tool_text(value: str) -> bool:
    compact = compact_text(value)
    return any(keyword in compact for keyword in TOOL_KEYWORDS)


def is_tool_group(group_name: str) -> bool:
    return any(hint in group_name for hint in TOOL_GROUP_HINTS)


def fetch(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=20)
    response.raise_for_status()
    return response.text


def build_index(session: requests.Session, delay: float) -> list[IndexEntry]:
    entries: list[IndexEntry] = []

    page = 0
    empty_streak = 0
    while empty_streak < 2:
        html = fetch(session, f"{BASE}/recipe-lab/list/recipe?p={page}")
        matches = RECIPELAB_LINK_RE.findall(html)
        if not matches:
            empty_streak += 1
        else:
            empty_streak = 0
            for path, title in matches:
                entries.append(IndexEntry(title=title.strip(), url=BASE + path.split("?")[0], source="recipe_lab"))
        page += 1
        time.sleep(delay)
    print(f"[index] recipe-lab: {page}페이지 훑음, {sum(1 for e in entries if e.source == 'recipe_lab')}개 수집", file=sys.stderr)

    page = 0
    empty_streak = 0
    cooking_count = 0
    while empty_streak < 2:
        html = fetch(session, f"{BASE}/cooking/list?p={page}")
        matches = COOKING_LINK_RE.findall(html)
        if not matches:
            empty_streak += 1
        else:
            empty_streak = 0
            for path, title in matches:
                entries.append(IndexEntry(title=title.strip(), url=BASE + path.split("?")[0], source="cooking"))
                cooking_count += 1
        page += 1
        time.sleep(delay)
    print(f"[index] cooking: {page}페이지 훑음, {cooking_count}개 수집", file=sys.stderr)

    return entries


def load_or_build_index(session: requests.Session, args: argparse.Namespace) -> list[IndexEntry]:
    cache_path = Path(args.index_cache)
    if cache_path.exists() and not args.rebuild_index:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"[index] 캐시에서 로드: {cache_path} ({len(raw)}건)", file=sys.stderr)
        return [IndexEntry(**item) for item in raw]

    entries = build_index(session, args.delay)
    cache_path.write_text(
        json.dumps([e.__dict__ for e in entries], ensure_ascii=False, indent=0),
        encoding="utf-8",
    )
    print(f"[index] 캐시 저장: {cache_path} (총 {len(entries)}건)", file=sys.stderr)
    return entries


def match_candidates(menu: str, index: list[IndexEntry]) -> list[IndexEntry]:
    compact_menu = compact_text(menu)
    recipe_lab = [e for e in index if e.source == "recipe_lab" and compact_menu in compact_text(e.title)]
    cooking = [e for e in index if e.source == "cooking" and compact_menu in compact_text(e.title)]
    return recipe_lab + cooking


def parse_ingredients(html: str) -> list[str]:
    parser = IngredientParser()
    parser.feed(html)
    names: list[str] = []
    for group, raw in parser.results:
        if is_tool_group(group):
            continue
        name = extract_ingredient_name(raw)
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

    index = load_or_build_index(session, args)

    data_rows: list[dict[str, str]] = []
    report_rows: list[dict[str, str]] = []

    for menu_idx, menu in enumerate(menus, start=1):
        candidates = match_candidates(menu, index)
        used = 0
        skipped_no_ingredients = 0
        ingredient_row_count = 0
        used_urls: set[str] = set()

        try:
            for candidate in candidates:
                if used >= args.max_recipes_per_menu:
                    break
                if candidate.url in used_urls:
                    continue
                try:
                    html = fetch(session, candidate.url)
                except Exception as exc:
                    print(f"[WARN] fetch failed {candidate.url}: {exc}", file=sys.stderr)
                    time.sleep(args.delay)
                    continue

                ingredients = parse_ingredients(html)
                time.sleep(args.delay)

                if not ingredients:
                    skipped_no_ingredients += 1
                    continue

                used += 1
                used_urls.add(candidate.url)
                ingredient_row_count += len(ingredients)
                for ingredient_name in ingredients:
                    data_rows.append(
                        {
                            "menu_query": menu,
                            "recipe_title": candidate.title,
                            "recipe_url": candidate.url,
                            "ingredient_name": ingredient_name,
                        }
                    )

            status = "ok" if used >= args.max_recipes_per_menu else (
                "partial" if used > 0 else "no_recipe"
            )
            report_rows.append(
                {
                    "menu_query": menu,
                    "candidate_count": str(len(candidates)),
                    "recipe_count": str(used),
                    "ingredient_row_count": str(ingredient_row_count),
                    "skipped_no_ingredients": str(skipped_no_ingredients),
                    "status": status,
                }
            )
            print(
                f"[{menu_idx:02d}/{len(menus)}] {menu}: 후보 {len(candidates)}개 중 {used}개 사용"
                f" ({skipped_no_ingredients}개 재료없어 skip), 재료 {ingredient_row_count}행",
            )
        except Exception as exc:
            report_rows.append(
                {
                    "menu_query": menu,
                    "candidate_count": str(len(candidates)),
                    "recipe_count": str(used),
                    "ingredient_row_count": str(ingredient_row_count),
                    "skipped_no_ingredients": str(skipped_no_ingredients),
                    "status": f"error: {exc}",
                }
            )
            print(f"[ERROR] {menu}: {exc}", file=sys.stderr)

    return data_rows, report_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl semie.cooking recipe ingredients.")
    parser.add_argument("--menu-file", default=str(DEFAULT_MENU_FILE), help="메뉴명 목록 txt 파일")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="크롤링 원본 CSV 출력 경로")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="메뉴별 수집 현황 CSV 출력 경로")
    parser.add_argument("--index-cache", default=str(DEFAULT_INDEX_CACHE), help="목록 인덱스 캐시 JSON 경로")
    parser.add_argument("--rebuild-index", action="store_true", help="캐시 무시하고 목록 인덱스를 새로 만듦")
    parser.add_argument("--max-recipes-per-menu", type=int, default=15, help="메뉴당 목표 레시피 수")
    parser.add_argument("--delay", type=float, default=0.15, help="요청 사이 대기 초")
    args = parser.parse_args()
    return args


def main() -> None:
    args = parse_args()
    rows, report = crawl(args)
    write_csv(Path(args.out), rows, ["menu_query", "recipe_title", "recipe_url", "ingredient_name"])
    write_csv(
        Path(args.report),
        report,
        ["menu_query", "candidate_count", "recipe_count", "ingredient_row_count", "skipped_no_ingredients", "status"],
    )
    print(f"\n완료: {args.out}")
    print(f"수집 행 수: {len(rows)}")
    print(f"리포트: {args.report}")


if __name__ == "__main__":
    main()
