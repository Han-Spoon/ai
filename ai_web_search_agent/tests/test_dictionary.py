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
