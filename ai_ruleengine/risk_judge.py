"""
한스푼 Rule Engine - 위험도 판정 (AI2 Step 6–9)

Step 6: 금지 재료 교집합 검사 → DANGER
Step 7: 매운맛 프로필 대조 → DANGER
Step 8: 애매함 플래그 관련성 판단 → CAUTION + need_gpt=True
        (hidden_rules 는 미사용 결정 → 애매함 플래그가 있으면 GPT 에스컬레이션)
Step 9: 최종 → SAFE

is_fish / is_duck 은 forbidden_tags 에 직접 포함되지 않지만
애매함 플래그 관련성 판단에서 내부 기준으로 활용된다.
"""

# 해물 계열 (has_unclear_jeotgal 관련성 판단용)
_SEAFOOD_TAGS = {"is_shrimp", "is_crab", "is_squid", "is_shellfish", "is_mackerel"}

# 동물성 계열 (has_hidden_animal 관련성 판단용)
_ANIMAL_TAGS = {
    "is_pork", "is_beef", "is_chicken", "is_duck",
    "is_fish",   # 내부 전용
} | _SEAFOOD_TAGS

# 비건 / 세미-베지테리언 타입
_VEGAN_TYPES = {"vegan", "lacto", "ovo", "lacto_ovo"}
_PLANT_TYPES = _VEGAN_TYPES | {"pesco"}


def judge_risk(
    menu_tags: set[str],
    forbidden_tags: set[str],
    ambiguity_flags: set[str],
    is_spicy_menu: bool,
    profile: dict,
) -> tuple[str, list[str], bool]:
    """
    Returns:
        (risk_level, hit_reasons, need_gpt)

    risk_level : "danger" | "caution" | "safe"
    hit_reasons: 원인 태그/플래그 목록 (정렬된 리스트)
    need_gpt   : GPT 에스컬레이션 필요 여부
    """
    # Step 6 — 금지 재료 직접 교집합
    hits = forbidden_tags & menu_tags
    if hits:
        return "danger", sorted(hits), False

    # Step 7 — 매운맛 비선호 (§2.4 별도 분기)
    if profile.get("is_spicy") is False and is_spicy_menu:
        return "danger", ["is_spicy"], False

    # Step 8 — 애매함 플래그 (hidden_rules 미사용 → 플래그 관련성만 판단)
    relevant = _relevant_ambiguity_flags(ambiguity_flags, forbidden_tags, profile)
    if relevant:
        return "caution", sorted(relevant), True

    # Step 9 — SAFE
    return "safe", [], False


def _relevant_ambiguity_flags(
    ambiguity_flags: set[str],
    forbidden_tags: set[str],
    profile: dict,
) -> set[str]:
    """
    애매함 플래그 중 사용자 프로필과 관련 있는 것만 반환.

    판단 기준:
      has_unclear_broth    : 채식/페스코 or 동물성 주재료가 forbidden 에 포함
      has_unclear_seasoning: is_alcohol 이 forbidden 에 포함
      has_unclear_jeotgal  : 해물류 알레르기 or 채식 (젓갈 = 새우/멸치 발효)
      has_hidden_animal    : 동물성 재료 제한이 있는 프로필
      has_variant          : forbidden 이 존재하는 모든 프로필 (변형 재료 불확실)
    """
    if not ambiguity_flags:
        return set()

    relevant: set[str] = set()
    vegan_type = profile.get("vegan_type")
    is_plant_diet = vegan_type in _PLANT_TYPES

    if "has_unclear_broth" in ambiguity_flags:
        broth_forbidden = forbidden_tags & (
            {"is_pork", "is_beef", "is_chicken", "is_shrimp", "is_crab", "is_shellfish"}
        )
        if broth_forbidden or is_plant_diet:
            relevant.add("has_unclear_broth")

    if "has_unclear_seasoning" in ambiguity_flags:
        if "is_alcohol" in forbidden_tags:
            relevant.add("has_unclear_seasoning")

    if "has_unclear_jeotgal" in ambiguity_flags:
        jeotgal_forbidden = forbidden_tags & _SEAFOOD_TAGS
        if jeotgal_forbidden or is_plant_diet:
            relevant.add("has_unclear_jeotgal")

    if "has_hidden_animal" in ambiguity_flags:
        animal_forbidden = forbidden_tags & _ANIMAL_TAGS
        if animal_forbidden or vegan_type in _VEGAN_TYPES:
            relevant.add("has_hidden_animal")

    if "has_variant" in ambiguity_flags and forbidden_tags:
        relevant.add("has_variant")

    return relevant
