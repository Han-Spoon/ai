"""Rule side of ⑥ ingredient extraction: find dictionary names in recipe text (⑥ §3-0).

The dictionary is ``data/ingredient_dictionary.csv``, built by ``build_dictionary.py``.
Names are returned as they appear in the text. Mapping them to standard ingredients
and tags is ④'s job, and names the dictionary misses are left for the LLM.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

DICTIONARY_PATH = Path(__file__).resolve().parent / "data" / "ingredient_dictionary.csv"
HANGUL_RUN_RE = re.compile(r"[가-힣]+")
# Particles after a one-letter name: "잣을 올린다", "굴과 함께". Without them a garnish
# written in a sentence was missed (잣 found in 4 of 9 menus, ⑥ §6-2). A wrong hit
# only adds a name, which is the safe side (2026-11-02).
PARTICLES = (
    "을", "를", "은", "는", "이", "가", "과", "와", "도", "만", "의", "로", "으로", "에",
    "랑", "이랑", "하고", "까지", "부터", "가루",
)
# Names that also end a word as grammar: "여름이라면", "아니라면", "그렇다라면" ("if ...").
# Read as 라면 they put a pork-tagged ingredient on 콩국수·닭강정·호두과자 (2026-11-12
# run). Skipped only at the end of a longer word right after these letters, so 신라면,
# 진라면, 라면사리 and "라면 1개" still count.
GRAMMAR_ENDINGS = {"라면": ("이", "니", "다", "으")}
# One-letter names that stand alone as grammar far more often than as food: "섞어주는 게
# 포인트예요" (게 = 것이). Read as 게 → 꽃게, they put crab on 돼지불백 (43%) and 로제떡볶이
# in the 2026-11-12 run, 13 times in one menu's blogs. Alone they count only before an
# amount ("게 2마리", "게 한 마리"); with a particle ("게를 손질") they still count.
ALONE_NEEDS_AMOUNT = frozenset({"게"})
AMOUNT_AFTER_RE = re.compile(r"\s*(?:\d|(?:한|두|세|네|다섯)\s*(?:마리|팩|개|kg|g))")


@lru_cache(maxsize=1)
def load_dictionary() -> dict[str, str]:
    """surface → canonical_name as crawled. The canonical is for reference only."""

    with DICTIONARY_PATH.open(encoding="utf-8", newline="") as f:
        return {row["surface"]: row["canonical_name"] for row in csv.DictReader(f)}


def find_ingredient_names(text: str) -> list[str]:
    """Dictionary names in ``text``, first-seen order, no duplicates.

    Each Hangul run is scanned left to right with the longest name first, so "새우젓"
    is not also read as "새우". A one-letter name (무, 파, 김 ...) only counts when it is
    the whole run, as in "무 1/4개", or is followed only by a particle ("잣을", "굴과");
    inside a word ("무엇", "김치찌개") it is skipped. A name in ``GRAMMAR_ENDINGS`` is
    skipped where it is that grammar ending ("이라면"), and one in ``ALONE_NEEDS_AMOUNT``
    standing alone counts only before an amount ("게 2마리", not "하는 게 좋아요").
    """

    names = load_dictionary()
    longest = max(map(len, names))
    found: dict[str, None] = {}
    for match in HANGUL_RUN_RE.finditer(text):
        run = match.group()
        if run in ALONE_NEEDS_AMOUNT and not AMOUNT_AFTER_RE.match(text, match.end()):
            continue
        if run in names:
            found.setdefault(run)
            continue
        if run[0] in names and run[1:] in PARTICLES:
            found.setdefault(run[0])
            continue
        start = 0
        while start < len(run):
            for end in range(min(len(run), start + longest), start + 1, -1):
                if run[start:end] in names and not _grammar_ending(run, start, end):
                    found.setdefault(run[start:end])
                    start = end
                    break
            else:
                start += 1
    return list(found)


def _grammar_ending(run: str, start: int, end: int) -> bool:
    before = GRAMMAR_ENDINGS.get(run[start:end])
    return bool(before) and start > 0 and end == len(run) and run[start - 1] in before


# Head of a recipe-data ingredient line: "오리슬라이스 1/2팩(500g)" → 오리슬라이스. Used only
# where the dictionary found nothing in that line (2026-11-17 check of the three priority
# sites: 7 of 1,116 lines, e.g. 오리슬라이스 (duck), 콩비지 (soy), 꽃빵 (wheat); ④ maps them).
_LINE_HEAD = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)?([가-힣][가-힣 ]{0,14}?)\s*(?=[\d(（½⅓¼⅔¾⅛~]|약간|적당|조금|한\s?줌|톡톡|$)"
)
RECIPE_DATA_PREFIX = "재료: "


def ingredient_line_names(content: str) -> list[str]:
    """Names the dictionary missed in schema.org ingredient lines (``pages.page_content``
    writes them as "재료: a, b, ..."). Page text without recipe data gives nothing here:
    its lines are sentences, not ingredient lines."""

    if not content.startswith(RECIPE_DATA_PREFIX):
        return []
    found: dict[str, None] = {}
    for line in content[len(RECIPE_DATA_PREFIX):].split(", "):
        if find_ingredient_names(line):
            continue
        head = _LINE_HEAD.match(line)
        if head and head.group(1).strip():
            found.setdefault(head.group(1).strip())
    return list(found)
