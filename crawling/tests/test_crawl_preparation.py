from __future__ import annotations

import argparse
import csv
from pathlib import Path

from crawling import crawl_data_tool
from crawling.filter_titles_additional_25 import ADDITIONAL_MENUS, keeps
from crawling.semie_crawler_additional_25 import IndexEntry
from crawling.semie_crawler_additional_25 import finished_menus as semie_finished_menus
from crawling.semie_crawler_additional_25 import match_candidates
from crawling.wtable_crawler_additional_25 import finished_menus as wtable_finished_menus


CRAWLING = Path(__file__).resolve().parents[1]
MENU_FILE = CRAWLING / "menu_queries_additional_25.txt"


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=crawl_data_tool.FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_additional_menu_file_contains_the_25_filter_menus_once() -> None:
    menus = crawl_data_tool.read_menus(MENU_FILE)

    assert len(menus) == 25
    assert len(set(menus)) == 25
    assert set(menus) == ADDITIONAL_MENUS


def test_additional_title_aliases_and_exclusions() -> None:
    accepted = {
        "고등어구이": "고갈비 맛있게 굽기",
        "오징어볶음": "매콤한 오징어 볶음",
        "청국장찌개": "구수한 청국장",
        "동태찌개": "얼큰한 동태매운탕",
        "아귀찜": "매콤한 아구찜",
        "황태해장국": "황태콩나물국 끈이기",
        "어묵볶음": "오뎅볶음 반찬",
        "소시지야채볶음": "쏘야볶음 만들기",
        "닭똥집볶음": "닭근위볶음 술안주",
        "북엇국": "시원한 북어국",
    }
    rejected = {
        "고등어구이": "묵은지 고등어조림",
        "오징어볶음": "오징어볶음칼국수",
        "두부조림": "바삭한 두부강정",
        "꽃게탕": "꽃게찜 정식",
        "해물찜": "해물파전",
        "골뱅이무침": "골뱅이소면",
        "닭발": "닭발 육수 삼계탕",
        "코다리조림": "코다리조림 한상",
    }

    assert all(keeps(menu, title) for menu, title in accepted.items())
    assert all(not keeps(menu, title) for menu, title in rejected.items())


def test_semie_candidate_matching_uses_aliases_and_exclusions() -> None:
    index = [
        IndexEntry("쏘야볶음", "https://example/1", "recipe_lab"),
        IndexEntry("소시지 야채 볶음 정식", "https://example/2", "recipe_lab"),
    ]

    result = match_candidates("소시지야채볶음", index)

    assert [item.url for item in result] == ["https://example/1"]


def test_resume_helpers_skip_only_non_error_reports(tmp_path: Path) -> None:
    report = tmp_path / "report.csv"
    report.write_text(
        "menu_query,recipe_count,ingredient_row_count,status\n"
        "고등어구이,3,20,ok\n"
        "오징어볶음,0,0,error: timeout\n",
        encoding="utf-8",
    )

    assert wtable_finished_menus(report) == {"고등어구이"}
    assert semie_finished_menus(report) == {"고등어구이"}


def test_validate_and_merge_write_only_staged_output(tmp_path: Path) -> None:
    menu_file = tmp_path / "menus.txt"
    menu_file.write_text("고등어구이\n", encoding="utf-8")
    base = tmp_path / "base.csv"
    additional = tmp_path / "additional.csv"
    staged = tmp_path / "staged.csv"
    write_csv(
        base,
        [{"menu_query": "김치찌개", "recipe_title": "김치찌개", "recipe_url": "https://example/base", "ingredient_name": "김치"}],
    )
    write_csv(
        additional,
        [{"menu_query": "고등어구이", "recipe_title": "고등어구이", "recipe_url": "https://example/new", "ingredient_name": "고등어"}],
    )

    validate_result = crawl_data_tool.validate(
        argparse.Namespace(
            input_csv=str(additional),
            menu_file=str(menu_file),
            check_titles=True,
            require_all_menus=True,
            fail_cross_menu_urls=False,
            low_recipe_threshold=0,
        )
    )
    merge_result = crawl_data_tool.merge(
        argparse.Namespace(
            base=str(base),
            additional=str(additional),
            menu_file=str(menu_file),
            out=str(staged),
            force=False,
        )
    )

    assert validate_result == 0
    assert merge_result == 0
    assert len(crawl_data_tool.read_rows(staged)) == 2
    assert len(crawl_data_tool.read_rows(base)) == 1


def test_combine_supports_cross_site_all_menu_validation(tmp_path: Path) -> None:
    menu_file = tmp_path / "menus.txt"
    menu_file.write_text("고등어구이\n오징어볶음\n", encoding="utf-8")
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    combined = tmp_path / "combined.csv"
    write_csv(
        first,
        [
            {
                "menu_query": "고등어구이",
                "recipe_title": "고등어구이",
                "recipe_url": "https://example/1",
                "ingredient_name": "고등어",
            }
        ],
    )
    write_csv(
        second,
        [
            {
                "menu_query": "오징어볶음",
                "recipe_title": "오징어 볶음",
                "recipe_url": "https://example/2",
                "ingredient_name": "오징어",
            }
        ],
    )

    result = crawl_data_tool.combine(
        argparse.Namespace(inputs=[str(first), str(second)], out=str(combined), force=False)
    )
    validate_result = crawl_data_tool.validate(
        argparse.Namespace(
            input_csv=str(combined),
            menu_file=str(menu_file),
            check_titles=True,
            require_all_menus=True,
            fail_cross_menu_urls=False,
            low_recipe_threshold=0,
        )
    )

    assert result == 0
    assert validate_result == 0
    assert {row["menu_query"] for row in crawl_data_tool.read_rows(combined)} == {
        "고등어구이",
        "오징어볶음",
    }
