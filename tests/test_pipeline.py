import os
from unittest.mock import patch

os.environ.setdefault("AZURE_OPENAI_API_KEY", "test-key")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")

from ai_result.main import build_final_result
from ai_result.models.final_output import FinalOutput


def _payload(**overrides):
    payload = {
        "menu_name_ko": "비빔밥",
        "is_spicy": None,
        "risk_level": "safe",
        "hit_tags": [],
        "triggered_flags": [],
        "forbidden_tags": [],
        "need_gpt": False,
        "escalation_case": [],
        "gpt_context": None,
        "risk_reasons": None,
    }
    payload.update(overrides)
    return payload


def _gpt_response(**overrides):
    response = {
        "hit_tags": [],
        "message_ko": "확인이 필요한 재료가 있을 수 있습니다",
        "message_en": "Some ingredients may need to be checked.",
        "message_ar": "قد تكون هناك مكونات تحتاج إلى التحقق.",
        "flag": "unknown",
        "question_ko": "이 메뉴에 제한 성분이 들어가나요?",
        "question_en": "Does this menu contain restricted ingredients?",
        "question_ar": "هل يحتوي هذا الطبق على مكونات مقيدة؟",
    }
    response.update(overrides)
    return response


def _assert_final_output(result):
    assert isinstance(result, FinalOutput)
    assert result.message is not None
    assert result.message.ko


def test_danger_returns_danger_template():
    result = build_final_result(
        _payload(
            menu_name_ko="삼겹살",
            risk_level="danger",
            hit_tags=["is_pork"],
            forbidden_tags=["is_pork"],
        )
    )

    _assert_final_output(result)
    assert result.risk_level == "danger"
    assert result.hits == ["is_pork"]
    assert result.owner_card is None


def test_safe_returns_safe_template():
    result = build_final_result(
        _payload(
            menu_name_ko="공기밥",
            risk_level="safe",
        )
    )

    _assert_final_output(result)
    assert result.risk_level == "safe"
    assert result.hits == []
    assert result.owner_card is None


def test_ambiguity_doenjang_jjigae_hidden_rule_keeps_caution():
    result = build_final_result(
        _payload(
            menu_name_ko="된장찌개",
            is_spicy=False,
            risk_level="caution",
            hit_tags=[],
            triggered_flags=["has_unclear_broth", "has_unclear_jeotgal"],
            forbidden_tags=[
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
            ],
            need_gpt=True,
            escalation_case=["ambiguity"],
            gpt_context={
                "base_menu": "된장찌개",
                "ingredients_explicit": ["된장", "두부", "애호박", "감자", "양파", "대파", "마늘", "고춧가루"],
                "explicit_tags": ["is_soybean"],
                "variant_tags": [],
            },
        )
    )

    _assert_final_output(result)
    assert result.risk_level == "caution"
    assert result.hits == ["is_fish"]
    assert result.owner_card is not None
    assert result.owner_card.flag == "has_unclear_broth"


def test_caution_turns_safe_when_no_hidden_rules_match_forbidden_tags():
    result = build_final_result(
        _payload(
            menu_name_ko="콩나물국",
            is_spicy=False,
            risk_level="caution",
            hit_tags=[],
            triggered_flags=[],
            forbidden_tags=["is_milk"],
            need_gpt=False,
            escalation_case=[],
            gpt_context=None,
        )
    )

    _assert_final_output(result)
    assert result.risk_level == "safe"
    assert result.hits == []
    assert result.owner_card is None


def test_caution_with_hidden_rule_hits_returns_owner_card():
    result = build_final_result(
        _payload(
            menu_name_ko="김치찌개",
            is_spicy=True,
            risk_level="caution",
            hit_tags=[],
            triggered_flags=["has_unclear_jeotgal"],
            forbidden_tags=["is_fish", "is_shellfish", "is_shrimp"],
            need_gpt=True,
            escalation_case=["ambiguity"],
            gpt_context={
                "base_menu": "김치찌개",
                "ingredients_explicit": ["배추김치", "김칫국물", "돼지고기", "두부", "대파"],
                "explicit_tags": [],
                "variant_tags": [],
            },
        )
    )

    _assert_final_output(result)
    assert result.risk_level == "caution"
    assert result.hits == ["is_fish", "is_shellfish"]
    assert result.owner_card is not None
    assert result.owner_card.flag == "has_unclear_jeotgal"


def test_unknown_menu_uses_gpt_template_and_keeps_caution():
    with patch(
        "ai_result.handlers.unknown_menu_handler.ask_gpt_json",
        return_value=_gpt_response(
            hit_tags=["is_pork"],
            message_ko="돼지고기 성분 확인이 필요합니다.",
            message_en="Please check for pork.",
            message_ar="يرجى التحقق من وجود لحم الخنزير.",
            flag="unknown_menu",
            question_ko="이 메뉴에 베이컨이나 햄이 들어가나요?",
            question_en="Does this menu contain bacon or ham?",
            question_ar="هل يحتوي هذا الطبق على لحم مقدد أو هام؟",
        ),
    ):
        result = build_final_result(
            _payload(
                menu_name_ko="버터갈릭쉬림프파스타",
                risk_level="caution",
                forbidden_tags=["is_pork"],
                need_gpt=True,
                escalation_case=["unknown_menu"],
            )
        )

    _assert_final_output(result)
    assert result.risk_level == "caution"
    assert result.hits == ["is_pork"]
    assert result.owner_card is not None
    assert result.owner_card.flag == "unknown_menu"


def test_unknown_remain_uses_gpt_owner_card_and_keeps_hits_empty():
    with patch(
        "ai_result.handlers.unknown_remain_handler.ask_gpt_json",
        return_value=_gpt_response(
            hit_tags=[],
            message_ko="추가 재료 확인이 필요합니다.",
            message_en="Additional ingredients need to be checked.",
            message_ar="يجب التحقق من المكونات الإضافية.",
            flag="unknown_remain",
            question_ko="셰프 특선 비빔밥에는 어떤 셰프 특선 재료가 들어가나요?",
            question_en="What special ingredients are in this chef's special bibimbap?",
            question_ar="ما هي المكونات الخاصة في هذا البيبيمباب الخاص؟",
        ),
    ):
        result = build_final_result(
            _payload(
                menu_name_ko="셰프특선비빔밥",
                risk_level="caution",
                forbidden_tags=["is_pork"],
                need_gpt=True,
                escalation_case=["unknown_remain"],
                gpt_context={
                    "base_menu": "비빔밥",
                    "ingredients_explicit": [],
                    "explicit_tags": [],
                    "variant_tags": ["셰프특선"],
                },
            )
        )

    _assert_final_output(result)
    assert result.risk_level == "caution"
    assert result.hits == []
    assert result.owner_card is not None
    assert result.owner_card.flag == "unknown_remain"
