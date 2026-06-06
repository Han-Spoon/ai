"""
한스푼 Rule Engine - 프로필 → 금지 태그 집합 매퍼 (AI2 §2)
"""

from constants import ALL_TAGS

# ── AI2 §2.1 종교 금지 태그 ──────────────────────────────────────────────────
_RELIGION_MAP: dict[str, set[str]] = {
    "halal":  {"is_pork", "is_alcohol"},
    "kosher": {"is_pork", "is_crab", "is_shrimp", "is_shellfish"},
    "hindu":  {"is_beef"},
}

# ── AI2 §2.2 채식 금지 태그 ────────────────────────────────────────────────────
_VEGAN_MAP: dict[str, set[str]] = {
    "vegan": {
        "is_pork", "is_beef", "is_chicken", "is_duck", "is_fish",
        "is_milk", "is_egg",
        "is_shrimp", "is_crab", "is_squid", "is_mackerel", "is_shellfish",
    },
    "lacto": {
        "is_pork", "is_beef", "is_chicken", "is_duck", "is_fish",
        "is_egg",
        "is_shrimp", "is_crab", "is_squid", "is_mackerel", "is_shellfish",
    },
    "ovo": {
        "is_pork", "is_beef", "is_chicken", "is_duck", "is_fish",
        "is_milk",
        "is_shrimp", "is_crab", "is_squid", "is_mackerel", "is_shellfish",
    },
    "lacto_ovo": {
        "is_pork", "is_beef", "is_chicken", "is_duck", "is_fish",
        "is_shrimp", "is_crab", "is_squid", "is_mackerel", "is_shellfish",
    },
    "pesco": {
        "is_pork", "is_beef", "is_chicken", "is_duck",
    },
}


def map_profile_to_forbidden(profile: dict) -> set[str]:
    """
    사용자 프로필 dict → 금지 재료 태그 set.

    profile 키:
      religion_type   : "halal" | "kosher" | "hindu" | None
      is_vegetarian   : bool
      vegetarian_type : "vegan" | "lacto" | "ovo" | "lacto_ovo" | "pesco" | None
      no_alcohol      : bool
      allergies       : list[str]  — is_ 태그명 (예: "is_egg", "is_shrimp")
      no_spicy        : bool  — 별도 분기 처리, 이 함수에서는 무시
    """
    forbidden: set[str] = set()

    religion = profile.get("religion_type")
    if religion and religion in _RELIGION_MAP:
        forbidden |= _RELIGION_MAP[religion]

    if profile.get("is_vegetarian"):
        vegan = profile.get("vegetarian_type")
        if vegan and vegan in _VEGAN_MAP:
            forbidden |= _VEGAN_MAP[vegan]

    # §2.3 — halal / no_alcohol 은 모두 is_alcohol 로 처리
    if profile.get("no_alcohol"):
        forbidden.add("is_alcohol")

    for allergy in profile.get("allergies", []):
        allergy = allergy.strip()
        if not allergy:
            continue
        tag = allergy if allergy.startswith("is_") else f"is_{allergy}"
        if tag in ALL_TAGS:
            forbidden.add(tag)

    return forbidden


def get_reason_type(tag: str, profile: dict) -> str:
    """태그 + 프로필 → reason_type 문자열 반환."""
    religion = profile.get("religion_type")
    if religion and tag in _RELIGION_MAP.get(religion, set()):
        return religion

    if profile.get("is_vegetarian"):
        vt = profile.get("vegetarian_type")
        if vt and tag in _VEGAN_MAP.get(vt, set()):
            return "vegetarian"

    allergy_tags = {
        a if a.startswith("is_") else f"is_{a}"
        for a in profile.get("allergies", [])
        if a
    }
    if tag in allergy_tags:
        return "allergy"

    if tag == "is_alcohol" and profile.get("no_alcohol"):
        return "alcohol"

    if tag == "is_spicy":
        return "spicy"

    return "other"
