"""Build the ⑥ rule-extraction dictionary from the crawled corpora.

    python -m ai_web_search_agent.build_dictionary

Writes ``data/ingredient_dictionary.csv`` (surface, canonical_name). Surfaces are the
standard ingredient names in ``crawling/ingredient_taxonomy.csv`` plus raw names the
crawled recipes used for them (``crawling/*_normalized.csv``). Raw names are kept
only when they are clean words seen at least twice, so one-off typos and notes such
as "생략가능" do not become dictionary entries. ⑥ only finds names with this; mapping
them to standard ingredients and tags stays with ④ (⑥ §1, §3-0).
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRAWLING = ROOT / "crawling"
OUTPUT = Path(__file__).resolve().parent / "data" / "ingredient_dictionary.csv"
CORPORA = ("10000recipe", "semie", "wtable")
MIN_COUNT = 2
WORD_RE = re.compile(r"^[가-힣]{1,10}$")
# Quantity, preference and instruction words that leak into raw ingredient cells.
NOISE_WORDS = (
    "생략", "또는", "혹은", "없으면", "약간", "적당", "기호", "선택", "취향", "조금",
    "한줌", "크게", "작게", "숟갈", "숟가락", "큰술", "작은술", "스푼", "컵", "가능",
    "정도", "개분", "인분", "토핑", "장식", "재료", "양념",
)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def is_clean_word(name: str) -> bool:
    return bool(WORD_RE.match(name)) and not any(word in name for word in NOISE_WORDS)


def build_entries() -> list[tuple[str, str]]:
    taxonomy = [row["name_ko"].strip() for row in _read(CRAWLING / "ingredient_taxonomy.csv")]
    pairs: Counter[tuple[str, str]] = Counter()
    for corpus in CORPORA:
        for row in _read(CRAWLING / f"{corpus}_normalized.csv"):
            pairs[(row["ingredient_raw"].strip(), row["ingredient_canonical"].strip())] += 1

    canonical_counts: Counter[str] = Counter()
    for (_, canonical), count in pairs.items():
        canonical_counts[canonical] += count
    canonicals = set(taxonomy) | {
        name
        for name, count in canonical_counts.items()
        if count >= MIN_COUNT and is_clean_word(name)
    }

    entries: dict[str, str] = {name: name for name in canonicals}
    # One surface can be normalized to different canonicals in different rows; keep
    # the most frequent so the file is stable. ④ re-normalizes anyway.
    best: dict[str, tuple[int, str]] = {}
    for (raw, canonical), count in pairs.items():
        if canonical not in canonicals or raw in entries:
            continue
        if count < MIN_COUNT or not is_clean_word(raw):
            continue
        if raw not in best or (count, canonical) > best[raw]:
            best[raw] = (count, canonical)
    entries.update({raw: canonical for raw, (_, canonical) in best.items()})
    return sorted(entries.items())


def main() -> None:
    entries = build_entries()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["surface", "canonical_name"])
        writer.writerows(entries)
    print(f"wrote {len(entries)} entries to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
