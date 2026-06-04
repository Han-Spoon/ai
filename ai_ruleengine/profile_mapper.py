"""
한스푼 Rule Engine - 프로필 → 금지 태그 집합 매퍼 (AI2 §2)

is_fish / is_duck 은 내부 전용 태그:
  - is_duck: 비건/페스코에서 직접 forbidden 에 추가 (조류이지만 19종 미포함)
  - is_fish: forbidden 에 직접 추가하지 않고, 애매함 플래그 관련성 판단에만 사용.
    (이유: 된장찌개처럼 레시피에 멸치가 명시되어도 육수 종류가 불분명한 경우가 많아
     직접 danger 처리 대신 has_unclear_broth 플래그로 GPT 에스컬레이션)
"""

from constants import ALL_TAGS

# ── AI2 §2.1 종교 금지 태그 ──────────────────────────────────────────────────
_RELIGION_MAP: dict[str, set[str]] = {
    "halal":  {"is_pork", "is_alcohol"},
    "kosher": {"is_pork", "is_crab", "is_shrimp", "is_shellfish"},
    "hindu":  {"is_beef"},
}

# ── AI2 §2.2 채식 금지 태그 (is_duck 추가, is_fish 는 내부 전용) ──────────────
_VEGAN_MAP: dict[str, set[str]] = {
    "vegan": {
        "is_pork", "is_beef", "is_chicken", "is_duck",
        "is_milk", "is_egg",
        "is_shrimp", "is_crab", "is_squid", "is_mackerel", "is_shellfish",
    },
    "lacto": {
        "is_pork", "is_beef", "is_chicken", "is_duck",
        "is_egg",
        "is_shrimp", "is_crab", "is_squid", "is_mackerel", "is_shellfish",
    },
    "ovo": {
        "is_pork", "is_beef", "is_chicken", "is_duck",
        "is_milk",
        "is_shrimp", "is_crab", "is_squid", "is_mackerel", "is_shellfish",
    },
    "lacto_ovo": {
        "is_pork", "is_beef", "is_chicken", "is_duck",
        "is_shrimp", "is_crab", "is_squid", "is_mackerel", "is_shellfish",
    },
    "pesco": {
        "is_pork", "is_beef", "is_chicken", "is_duck",
    },
}

# 알레르기 한국어 명칭 → 태그 매핑 (편의 제공)
_KO_ALLERGY_MAP: dict[str, str] = {
    "달걀": "is_egg",   "계란": "is_egg",
    "우유": "is_milk",  "유제품": "is_milk",
    "메밀": "is_buckwheat",
    "땅콩": "is_peanut",
    "대두": "is_soybean", "콩": "is_soybean",
    "밀": "is_wheat",
    "잣": "is_pinenut",
    "호두": "is_walnut",
    "게": "is_crab",    "꽃게": "is_crab",
    "새우": "is_shrimp",
    "오징어": "is_squid",
    "고등어": "is_mackerel",
    "조개": "is_shellfish", "조갯살": "is_shellfish",
    "복숭아": "is_peach",
    "토마토": "is_tomato",
    "닭고기": "is_chicken", "닭": "is_chicken",
    "돼지고기": "is_pork",  "돼지": "is_pork",
    "소고기": "is_beef",    "쇠고기": "is_beef",
    "아황산": "is_sulfite",
    "갑각류": "is_crab",    # 갑각류 표기 편의 → 게 대표로 매핑
}


def map_profile_to_forbidden(profile: dict) -> set[str]:
    """
    사용자 프로필 dict → 금지 재료 태그 set.

    profile 키:
      religion_type : "halal" | "kosher" | "hindu" | None
      vegan_type    : "vegan" | "lacto" | "ovo" | "lacto_ovo" | "pesco" | None
      no_alcohol    : bool
      allergies     : list[str]  — "crab", "shrimp", "새우" 등 영/한 혼용 가능
      is_spicy      : True | False | None  — 별도 분기 처리, 이 함수에서는 무시
    """
    forbidden: set[str] = set()

    religion = profile.get("religion_type")
    if religion and religion in _RELIGION_MAP:
        forbidden |= _RELIGION_MAP[religion]

    vegan = profile.get("vegan_type")
    if vegan and vegan in _VEGAN_MAP:
        forbidden |= _VEGAN_MAP[vegan]

    # §2.3 — halal / no_alcohol 은 모두 is_alcohol 로 처리
    if profile.get("no_alcohol"):
        forbidden.add("is_alcohol")

    for allergy in profile.get("allergies", []):
        allergy = allergy.strip()
        if not allergy:
            continue
        # 한국어 매핑 시도
        if allergy in _KO_ALLERGY_MAP:
            forbidden.add(_KO_ALLERGY_MAP[allergy])
            continue
        # is_ 접두어 처리
        tag = allergy if allergy.startswith("is_") else f"is_{allergy}"
        if tag in ALL_TAGS:
            forbidden.add(tag)

    return forbidden
