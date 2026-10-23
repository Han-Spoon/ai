"""User profile → forbidden constraint tags (docs/8-agent-xai.md §2-1 step 1).

Religion and vegetarian tables are copied from ai_ruleengine/profile_mapper.py,
the source of truth named in ⑧ §2-1.
"""

from __future__ import annotations

from ai_supervisor.contracts.common import UserProfile

RELIGION_TAGS: dict[str, frozenset[str]] = {
    "halal": frozenset({"is_pork", "is_alcohol"}),
    "kosher": frozenset({"is_pork", "is_crab", "is_shrimp", "is_shellfish", "is_squid"}),
    "hindu": frozenset({"is_beef"}),
}

_MEAT = frozenset({"is_pork", "is_beef", "is_chicken", "is_duck"})
_SEAFOOD = frozenset({"is_fish", "is_shrimp", "is_crab", "is_squid", "is_mackerel", "is_shellfish"})

VEGETARIAN_TAGS: dict[str, frozenset[str]] = {
    "vegan": _MEAT | _SEAFOOD | {"is_milk", "is_egg"},
    "lacto": _MEAT | _SEAFOOD | {"is_egg"},
    "ovo": _MEAT | _SEAFOOD | {"is_milk"},
    "lacto_ovo": _MEAT | _SEAFOOD,
    "pesco": _MEAT,
}


def forbidden_tags(profile: UserProfile) -> frozenset[str]:
    """Every restriction becomes one tag set so all users go through the same rules."""

    tags: set[str] = set()
    if profile.religion_type is not None:
        tags |= RELIGION_TAGS[profile.religion_type]
    if profile.vegetarian_type is not None:
        tags |= VEGETARIAN_TAGS[profile.vegetarian_type]
    if profile.no_alcohol:
        tags.add("is_alcohol")
    if profile.no_spicy:
        tags.add("is_spicy")
    tags.update(profile.allergies)
    return frozenset(tags)
