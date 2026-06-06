"""최종 템플릿에 들어갈 안내 문구를 생성한다."""

from ai_result.models.final_output import FinalMessage


TAG_LABEL_KO = {
    "is_egg": "달걀",
    "is_milk": "유제품",
    "is_buckwheat": "메밀",
    "is_peanut": "땅콩",
    "is_soybean": "대두",
    "is_wheat": "밀",
    "is_pinenut": "잣",
    "is_walnut": "호두",
    "is_crab": "게",
    "is_shrimp": "새우",
    "is_squid": "오징어류",
    "is_mackerel": "고등어",
    "is_shellfish": "조개류",
    "is_peach": "복숭아",
    "is_tomato": "토마토",
    "is_chicken": "닭고기",
    "is_pork": "돼지고기",
    "is_beef": "소고기",
    "is_sulfite": "아황산염",
    "is_fish": "생선",
    "is_duck": "오리고기",
    "is_alcohol": "알코올",
    "is_spicy": "매운맛",
    "is_pinenut": "잣",
    "is_walnut": "호두",
}

TAG_LABEL_EN = {
    "is_egg": "egg",
    "is_milk": "dairy",
    "is_buckwheat": "buckwheat",
    "is_peanut": "peanut",
    "is_soybean": "soybean",
    "is_wheat": "wheat",
    "is_pinenut": "pine nut",
    "is_walnut": "walnut",
    "is_crab": "crab",
    "is_shrimp": "shrimp",
    "is_squid": "squid",
    "is_mackerel": "mackerel",
    "is_shellfish": "shellfish",
    "is_peach": "peach",
    "is_tomato": "tomato",
    "is_chicken": "chicken",
    "is_pork": "pork",
    "is_beef": "beef",
    "is_sulfite": "sulfite",
    "is_fish": "fish",
    "is_duck": "duck",
    "is_alcohol": "alcohol",
    "is_spicy": "spicy ingredients",
    "is_pinenut": "pine nut",
    "is_walnut": "walnut",
}

TAG_LABEL_AR = {
    "is_egg": "بيض",
    "is_milk": "منتجات الألبان",
    "is_buckwheat": "الحنطة السوداء",
    "is_peanut": "فول سوداني",
    "is_soybean": "فول الصويا",
    "is_wheat": "قمح",
    "is_pinenut": "صنوبر",
    "is_walnut": "جوز",
    "is_crab": "سرطان البحر",
    "is_shrimp": "روبيان",
    "is_squid": "حبار",
    "is_mackerel": "ماكريل",
    "is_shellfish": "المحار",
    "is_peach": "خوخ",
    "is_tomato": "طماطم",
    "is_chicken": "دجاج",
    "is_pork": "لحم الخنزير",
    "is_beef": "لحم البقر",
    "is_sulfite": "كبريتيت",
    "is_fish": "سمك",
    "is_duck": "بط",
    "is_alcohol": "كحول",
    "is_spicy": "مكونات حارة",
    "is_pinenut": "صنوبر",
    "is_walnut": "جوز",
}


def _tag_names(hit_tags: list[str], label_map: dict[str, str]) -> str:
    return ", ".join(label_map.get(tag, tag) for tag in hit_tags)


def build_tag_names(hit_tags: list[str], language: str) -> str:
    label_maps = {
        "ko": TAG_LABEL_KO,
        "en": TAG_LABEL_EN,
        "ar": TAG_LABEL_AR,
    }
    return _tag_names(hit_tags, label_maps[language])


def _build_ko(hit_tags: list[str], risk_level: str) -> str:
    if risk_level == "safe":
        return "안전하게 드실 수 있어요."

    if not hit_tags:
        return "확인이 필요한 재료가 있을 수 있어요."

    names = _tag_names(hit_tags, TAG_LABEL_KO)
    if risk_level == "danger":
        return f"{names} 성분이 포함되어 있어요."

    return f"{names} 성분이 포함되어 있을 수 있어요."


def _build_en(hit_tags: list[str], risk_level: str) -> str:
    if risk_level == "safe":
        return "This menu is safe for you."

    if not hit_tags:
        return "Some ingredients may need to be checked."

    names = _tag_names(hit_tags, TAG_LABEL_EN)
    if risk_level == "danger":
        return f"This menu contains {names}."

    return f"This menu may contain {names}."


def _build_ar(hit_tags: list[str], risk_level: str) -> str:
    if risk_level == "safe":
        return "هذا الطبق آمن لك."

    if not hit_tags:
        return "قد تكون هناك مكونات تحتاج إلى التحقق."

    names = _tag_names(hit_tags, TAG_LABEL_AR)
    if risk_level == "danger":
        return f"يحتوي هذا الطبق على {names}."

    return f"قد يحتوي هذا الطبق على {names}."


def build_message(
    hit_tags: list[str],
    risk_level: str,
    ko_override: str | None = None,
    en_override: str | None = None,
    ar_override: str | None = None,
) -> FinalMessage:
    """hit_tags와 risk_level로 ko/en/ar 문구를 생성한다."""
    return FinalMessage(
        ko=ko_override or _build_ko(hit_tags, risk_level),
        en=en_override or _build_en(hit_tags, risk_level),
        ar=ar_override or _build_ar(hit_tags, risk_level),
    )
