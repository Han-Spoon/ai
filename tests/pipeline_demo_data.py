VEGAN_FORBIDDEN_TAGS = [
    "is_beef",
    "is_chicken",
    "is_duck",
    "is_egg",
    "is_milk",
    "is_pork",
    "is_crab",
    "is_shrimp",
    "is_squid",
    "is_mackerel",
    "is_shellfish",
    "is_fish",
]


def gpt_context(
    base_menu: str | None,
    ingredients_explicit: list[str],
    explicit_tags: list[str] | None = None,
    variant_tags: list[str] | None = None,
) -> dict:
    return {
        "base_menu": base_menu,
        "ingredients_explicit": ingredients_explicit,
        "explicit_tags": explicit_tags or [],
        "variant_tags": variant_tags or [],
    }


def payload(**overrides):
    data = {
        "menu_name_ko": "비빔밥",
        "is_spicy": False,
        "risk_level": "safe",
        "hit_tags": [],
        "triggered_flags": [],
        "forbidden_tags": [],
        "need_gpt": False,
        "escalation_case": [],
        "gpt_context": None,
        "risk_reasons": None,
    }
    data.update(overrides)
    return data


PIPELINE_CASES = [
    {
        "case_id": "01_danger_halal_pork",
        "description": "할랄 사용자가 삼겹살을 선택해 룰엔진 1차 hit_tags에서 돼지고기가 바로 확정된다.",
        "assumed_user_profile": {
            "religion_type": "halal",
            "vegan_type": None,
            "allergies": [],
            "forbidden_reason": "돼지고기 섭취 제한",
        },
        "rule_engine_input": payload(
            menu_name_ko="삼겹살",
            is_spicy=False,
            risk_level="danger",
            hit_tags=["is_pork"],
            triggered_flags=[],
            forbidden_tags=["is_pork"],
            need_gpt=False,
            escalation_case=[],
            gpt_context=gpt_context(
                base_menu="삼겹살",
                ingredients_explicit=["돼지고기", "소금", "후추"],
                explicit_tags=["is_pork"],
            ),
        ),
    },
    {
        "case_id": "02_safe_vegan_sanchae_bibimbap",
        "description": "비건 사용자가 산채비빔밥을 선택했고 룰엔진이 금지 태그가 없다고 판단해 safe를 넘긴다.",
        "assumed_user_profile": {
            "religion_type": None,
            "vegan_type": "vegan",
            "allergies": [],
            "forbidden_reason": "육류, 어류, 달걀, 유제품 제한",
        },
        "rule_engine_input": payload(
            menu_name_ko="산채비빔밥",
            is_spicy=False,
            risk_level="safe",
            hit_tags=[],
            triggered_flags=[],
            forbidden_tags=VEGAN_FORBIDDEN_TAGS,
            need_gpt=False,
            escalation_case=[],
            gpt_context=gpt_context(
                base_menu="비빔밥",
                ingredients_explicit=["밥", "고사리", "도라지", "시금치", "콩나물", "고추장"],
                explicit_tags=["is_soybean"],
                variant_tags=["산채"],
            ),
        ),
    },
    {
        "case_id": "03_ambiguity_to_caution_vegan_doenjang_jjigae",
        "description": "비건 사용자의 된장찌개가 ambiguity로 넘어오고, 된장찌개 hidden_rules의 멸치육수 가능성이 is_fish caution을 만든다.",
        "assumed_user_profile": {
            "religion_type": None,
            "vegan_type": "vegan",
            "allergies": [],
            "forbidden_reason": "동물성 재료 전반 제한",
        },
        "rule_engine_input": payload(
            menu_name_ko="된장찌개",
            is_spicy=False,
            risk_level="caution",
            hit_tags=[],
            triggered_flags=["has_unclear_broth", "has_unclear_jeotgal"],
            forbidden_tags=VEGAN_FORBIDDEN_TAGS,
            need_gpt=True,
            escalation_case=["ambiguity"],
            gpt_context=gpt_context(
                base_menu="된장찌개",
                ingredients_explicit=["된장", "두부", "애호박", "감자", "양파", "대파", "마늘", "고춧가루"],
                explicit_tags=["is_soybean"],
            ),
        ),
    },
    {
        "case_id": "04_ambiguity_to_caution_shellfish_kimchi_jjigae",
        "description": "어류/갑각류 알레르기 사용자의 김치찌개가 ambiguity로 넘어오고, 김치 hidden_rules가 fish/shellfish hits를 만든다.",
        "assumed_user_profile": {
            "religion_type": None,
            "vegan_type": None,
            "allergies": ["fish", "shrimp", "shellfish"],
            "forbidden_reason": "어류 및 갑각류/조개류 알레르기",
        },
        "rule_engine_input": payload(
            menu_name_ko="김치찌개",
            is_spicy=True,
            risk_level="caution",
            hit_tags=[],
            triggered_flags=["has_unclear_jeotgal"],
            forbidden_tags=["is_fish", "is_shellfish", "is_shrimp"],
            need_gpt=True,
            escalation_case=["ambiguity"],
            gpt_context=gpt_context(
                base_menu="김치찌개",
                ingredients_explicit=["배추김치", "김칫국물", "돼지고기", "두부", "대파"],
                explicit_tags=["is_pork"],
            ),
        ),
    },
    {
        "case_id": "05_unknown_menu_halal_bacon_cream_pasta",
        "description": "할랄 사용자가 DB에 없는 베이컨크림파스타를 선택해 unknown_menu로 넘어오고 GPT가 분석한다.",
        "assumed_user_profile": {
            "religion_type": "halal",
            "vegan_type": None,
            "allergies": [],
            "forbidden_reason": "돼지고기와 알코올 제한",
        },
        "rule_engine_input": payload(
            menu_name_ko="베이컨크림파스타",
            is_spicy=False,
            risk_level="caution",
            hit_tags=[],
            triggered_flags=[],
            forbidden_tags=["is_pork", "is_alcohol"],
            need_gpt=True,
            escalation_case=["unknown_menu"],
            gpt_context=gpt_context(
                base_menu=None,
                ingredients_explicit=[],
                explicit_tags=[],
            ),
        ),
    },
    {
        "case_id": "06_unknown_remain_vegan_chef_special_bibimbap",
        "description": "비건 사용자의 셰프특선비빔밥에서 base_menu는 비빔밥으로 잡혔지만 셰프특선 variant가 미인식되어 unknown_remain으로 넘어온다.",
        "assumed_user_profile": {
            "religion_type": None,
            "vegan_type": "vegan",
            "allergies": [],
            "forbidden_reason": "동물성 재료 전반 제한",
        },
        "rule_engine_input": payload(
            menu_name_ko="셰프특선비빔밥",
            is_spicy=False,
            risk_level="caution",
            hit_tags=[],
            triggered_flags=[],
            forbidden_tags=VEGAN_FORBIDDEN_TAGS,
            need_gpt=True,
            escalation_case=["unknown_remain"],
            gpt_context=gpt_context(
                base_menu="비빔밥",
                ingredients_explicit=["밥", "나물", "고추장", "참기름"],
                explicit_tags=["is_soybean"],
                variant_tags=["셰프특선"],
            ),
        ),
    },
]
