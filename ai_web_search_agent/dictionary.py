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


@lru_cache(maxsize=1)
def load_dictionary() -> dict[str, str]:
    """surface → canonical_name as crawled. The canonical is for reference only."""

    with DICTIONARY_PATH.open(encoding="utf-8", newline="") as f:
        return {row["surface"]: row["canonical_name"] for row in csv.DictReader(f)}


def find_ingredient_names(text: str) -> list[str]:
    """Dictionary names in ``text``, first-seen order, no duplicates.

    Each Hangul run is scanned left to right with the longest name first, so "새우젓"
    is not also read as "새우". A one-letter name (무, 파, 김 ...) only counts when it is
    the whole run, as in "무 1/4개"; inside a word ("무엇", "김치찌개") it is skipped.
    """

    names = load_dictionary()
    longest = max(map(len, names))
    found: dict[str, None] = {}
    for run in HANGUL_RUN_RE.findall(text):
        if run in names:
            found.setdefault(run)
            continue
        start = 0
        while start < len(run):
            for end in range(min(len(run), start + longest), start + 1, -1):
                if run[start:end] in names:
                    found.setdefault(run[start:end])
                    start = end
                    break
            else:
                start += 1
    return list(found)
