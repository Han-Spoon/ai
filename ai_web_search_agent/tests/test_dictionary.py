"""Ingredient dictionary for ⑥ rule extraction (⑥ §3-0, #119)."""

import csv
import re

from ai_web_search_agent import find_ingredient_names, load_dictionary
from ai_web_search_agent.build_dictionary import CRAWLING, NOISE_WORDS, build_entries


def test_dictionary_file_matches_the_crawled_corpora() -> None:
    """Rebuild with ``python -m ai_web_search_agent.build_dictionary`` when this fails."""

    assert sorted(load_dictionary().items()) == build_entries()


def test_every_standard_ingredient_is_in_the_dictionary() -> None:
    with (CRAWLING / "ingredient_taxonomy.csv").open(encoding="utf-8-sig") as f:
        taxonomy = {row["name_ko"].strip() for row in csv.DictReader(f)}
    dictionary = load_dictionary()
    assert taxonomy <= dictionary.keys()
    assert all(dictionary[name] == name for name in taxonomy)


def test_crawled_aliases_are_clean_words() -> None:
    for surface, canonical in load_dictionary().items():
        assert re.fullmatch(r"[가-힣]+", surface), surface
        if surface != canonical:
            assert not any(word in surface for word in NOISE_WORDS), surface


def test_ingredient_list_names_are_found_as_written() -> None:
    text = "돼지고기 300g, 다진마늘 1큰술, 무 1/4개, 꽈리고추 10개, 새우젓 약간"
    assert find_ingredient_names(text) == ["돼지고기", "다진마늘", "무", "꽈리고추", "새우젓"]


def test_longest_name_wins() -> None:
    assert find_ingredient_names("새우젓") == ["새우젓"]


def test_one_letter_name_needs_a_whole_word() -> None:
    assert find_ingredient_names("무엇보다 맛있는 요리") == []
    assert find_ingredient_names("무1/4개, 파 약간") == ["무", "파"]


def test_names_are_not_repeated() -> None:
    assert find_ingredient_names("양파 1개를 썰고 남은 양파는 볶는다") == ["양파"]


def test_one_letter_name_with_a_particle_is_found() -> None:
    # Garnishes are often written in a sentence: "잣을 올린다" (2026-11-02).
    assert find_ingredient_names("잣을 올려 마무리하고 굴과 무를 넣는다") == ["잣", "굴", "무"]
    assert find_ingredient_names("무엇을 넣을까") == []



def test_ramen_as_an_if_ending_is_not_an_ingredient() -> None:
    # 2026-11-12 run: "~이라면" read as 라면 put a pork-tagged ingredient on 콩국수.
    assert find_ingredient_names("여름이라면 시원하게, 아니라면 따뜻하게, 그렇다라면") == []
    # Real ramen still counts, also as a product name ("신라면" is a dictionary name for 라면
    # since the 2026-11-22 crawl, so the longer name is found).
    assert find_ingredient_names("신라면 1봉지") == ["신라면"]
    assert load_dictionary()["신라면"] == "라면"
    assert find_ingredient_names("라면사리 넣고 라면을 끓인다") == ["라면사리", "라면"]


def test_crab_alone_needs_an_amount() -> None:
    # 2026-11-12 second run: "섞어주는 게 포인트예요" (게 = 것이) read as 꽃게 put crab
    # on 돼지불백. Alone, 게 counts only before an amount; with a particle it counts.
    assert find_ingredient_names("섞어주는 게 포인트예요. 늘리는 게 안전해요") == []
    assert find_ingredient_names("게 2마리") == ["게"]
    assert find_ingredient_names("게 한 마리") == ["게"]
    assert find_ingredient_names("게를 깨끗이 손질한다") == ["게"]


def test_recipe_data_lines_give_names_the_dictionary_missed() -> None:
    from ai_web_search_agent.dictionary import ingredient_line_names

    # 2026-11-17 check of the priority sites: ④ maps these (오리고기·콩·빵), the dictionary
    # had no entry, so with the LLM down they were lost.
    content = "재료: 오리슬라이스 1/2팩(500g), 콩비지 1봉 (300g), 꽃빵 8~9개, 양파 1개, 소금 약간"
    assert ingredient_line_names(content) == ["오리슬라이스", "콩비지", "꽃빵"]
    # Page text (no recipe data) is sentences, so nothing is taken from it.
    assert ingredient_line_names("오리슬라이스를 넣고 볶아요") == []
