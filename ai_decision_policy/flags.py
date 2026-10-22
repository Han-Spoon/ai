"""Menu ambiguity flags and the owner's answers to them (⑧ §1-1 table, §2-1 step 5-2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_supervisor.contracts.common import AmbiguityFlag, FlagConfirmation

_MEAT_BROTH = frozenset(
    {"is_pork", "is_beef", "is_chicken", "is_shrimp", "is_crab", "is_shellfish"}
)
_JEOTGAL = frozenset(
    {"is_fish", "is_shrimp", "is_crab", "is_squid", "is_shellfish", "is_mackerel"}
)
_ANIMAL = frozenset({"is_pork", "is_beef", "is_chicken", "is_duck"}) | _JEOTGAL

# Tags each flag can hide. has_variant is handled separately: it can hide anything.
FLAG_TAGS: dict[str, frozenset[str]] = {
    "has_unclear_broth": _MEAT_BROTH,
    "has_unclear_seasoning": frozenset({"is_alcohol"}),
    "has_unclear_jeotgal": _JEOTGAL,
    "has_hidden_animal": _ANIMAL,
}

FlagState = Literal["resolved", "danger", "unresolved"]


@dataclass(frozen=True)
class FlagCheck:
    flag: AmbiguityFlag
    ask_tags: list[str]
    state: FlagState
    matched_tags: list[str]


def ask_tags(flag: AmbiguityFlag, forbidden: frozenset[str]) -> frozenset[str]:
    """Tags worth asking about: the flag's tags that this user cannot eat."""

    if flag == "has_variant":
        return forbidden
    return FLAG_TAGS[flag] & forbidden


def check_flag(
    flag: AmbiguityFlag,
    forbidden: frozenset[str],
    answers: list[FlagConfirmation],
) -> FlagCheck | None:
    """None when the flag is unrelated to this user. A "present" answer only becomes
    DANGER when every asked tag is forbidden for this user; otherwise it stays unresolved."""

    asked = ask_tags(flag, forbidden)
    if not asked:
        return None
    usable = [a for a in answers if a.ambiguity_flag == flag and a.override_eligible]
    for answer in usable:
        tags = set(answer.constraint_tags)
        if answer.status == "present" and tags and tags <= forbidden:
            return FlagCheck(flag, sorted(asked), "danger", sorted(tags))
    for answer in usable:
        if answer.status == "absent" and asked <= set(answer.constraint_tags):
            return FlagCheck(flag, sorted(asked), "resolved", [])
    return FlagCheck(flag, sorted(asked), "unresolved", [])
