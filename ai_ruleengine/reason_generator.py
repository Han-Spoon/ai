"""
한스푼 Rule Engine - 위험 이유 문장 생성 (AI2 Step 10)

한국어(reason_ko)만 생성. 영어/아랍어는 후속 LLM 단계 책임.
"""

_TAG_REASON_KO: dict[str, str] = {
    # 알레르기 19종
    "is_egg":       "달걀(계란류) 성분이 포함되어 있습니다",
    "is_milk":      "유제품(우유·치즈·버터 등) 성분이 포함되어 있습니다",
    "is_buckwheat": "메밀 성분이 포함되어 있습니다",
    "is_peanut":    "땅콩 성분이 포함되어 있습니다",
    "is_soybean":   "대두(콩류) 성분이 포함되어 있습니다",
    "is_wheat":     "밀(글루텐) 성분이 포함되어 있습니다",
    "is_pinenut":   "잣 성분이 포함되어 있습니다",
    "is_walnut":    "호두 성분이 포함되어 있습니다",
    "is_crab":      "게 성분이 포함되어 있습니다",
    "is_shrimp":    "새우 성분이 포함되어 있습니다",
    "is_squid":     "오징어류 성분이 포함되어 있습니다",
    "is_mackerel":  "고등어 성분이 포함되어 있습니다",
    "is_shellfish": "조개류(굴·홍합 등) 성분이 포함되어 있습니다",
    "is_peach":     "복숭아 성분이 포함되어 있습니다",
    "is_tomato":    "토마토 성분이 포함되어 있습니다",
    "is_chicken":   "닭고기 성분이 포함되어 있습니다",
    "is_pork":      "돼지고기 성분이 포함되어 있습니다",
    "is_beef":      "소고기 성분이 포함되어 있습니다",
    "is_sulfite":   "아황산염 성분이 포함될 수 있습니다",
    # 추가 내부 태그
    "is_alcohol":   "알코올·주류 성분이 포함될 수 있습니다",
    "is_fish":      "어류 성분이 포함되어 있습니다",
    "is_duck":      "오리고기 성분이 포함되어 있습니다",
    # 매운맛
    "is_spicy":     "매운 메뉴입니다. 매운맛을 선호하지 않으시는 분께 적합하지 않을 수 있습니다",
    # 애매함 플래그
    "has_unclear_broth":
        "육수의 종류가 식당마다 달라 정확한 성분을 확인하기 어렵습니다",
    "has_unclear_seasoning":
        "양념(맛술·청주 등)의 사용 여부가 불분명하여 알코올 성분이 포함될 수 있습니다",
    "has_unclear_jeotgal":
        "젓갈류(새우젓·멸치젓 등)의 사용 여부가 불분명합니다",
    "has_hidden_animal":
        "레시피에 명시되지 않은 동물성 재료가 사용될 수 있습니다",
    "has_variant":
        "동일 메뉴명이라도 재료가 다를 수 있는 변형 메뉴가 존재합니다",
    # 미확인
    "unknown_menu":
        "메뉴 DB에 없는 메뉴로, 정확한 성분을 확인하기 어렵습니다",
    "unknown_remain":
        "메뉴명에 인식되지 않은 재료가 포함되어 있어 정확한 분류가 어렵습니다",
}


def generate_reason_ko(risk_reasons: list[str]) -> str | None:
    """
    risk_reasons 리스트를 받아 한국어 이유 문장을 반환.
    이유가 없으면 None.
    """
    if not risk_reasons:
        return None

    sentences = []
    for reason in risk_reasons:
        sentence = _TAG_REASON_KO.get(reason)
        if sentence:
            sentences.append(sentence)
        else:
            sentences.append(f"[{reason}] 성분 또는 조리 방식에 주의가 필요합니다")

    return " / ".join(sentences)
