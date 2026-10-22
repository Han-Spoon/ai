"""Versioned, immutable dictionaries used by deterministic cleaning."""

from types import MappingProxyType

DICTIONARY_VERSION = "2026-10-v1"

# Entries may correct spelling, but must not remove ingredient or variant meaning.
NORMALIZATION_REPLACEMENTS = MappingProxyType(
    {
        "가재미탕": "가자미탕",
        "돈까스": "돈가스",
        "찌게": "찌개",
    }
)

MULTILINGUAL_ALIASES = MappingProxyType(
    {
        "bibimbap": "비빔밥",
        "bulgogi": "불고기",
        "kimchijjigae": "김치찌개",
        "kimchistew": "김치찌개",
    }
)

SECTION_HEADERS = frozenset(
    {
        "메뉴",
        "메뉴판",
        "식사류",
        "안주류",
        "주류",
        "음료",
        "음료류",
        "추천메뉴",
        "대표메뉴",
    }
)

__all__ = [
    "DICTIONARY_VERSION",
    "MULTILINGUAL_ALIASES",
    "NORMALIZATION_REPLACEMENTS",
    "SECTION_HEADERS",
]
