import unittest
from unittest.mock import patch

from ai_result.main import build_final_result


class UnknownMenuHandlerTest(unittest.TestCase):
    def test_unknown_menu_uses_gpt_template(self):
        with patch(
            "ai_result.handlers.unknown_menu_handler.ask_gpt_json",
            return_value={
                "hit_tags": ["is_pork"],
                "message_ko": "돼지고기 성분 확인이 필요합니다",
                "message_en": "Please check for pork.",
                "message_ar": "يرجى التحقق من وجود لحم الخنزير.",
                "flag": "unknown_menu",
                "question_ko": "이 메뉴에 돼지고기가 들어가나요?",
            },
        ):
            result = build_final_result(
                {
                    "menu_name_ko": "버터갈릭쉬림프파스타",
                    "risk_level": "caution",
                    "hit_tags": [],
                    "triggered_flags": [],
                    "forbidden_tags": ["is_pork"],
                    "need_gpt": True,
                    "escalation_case": ["unknown_menu"],
                }
            )
        self.assertEqual(result.risk_level, "caution")
        self.assertEqual(result.hits, ["is_pork"])
        self.assertEqual(result.message.en, "Please check for pork.")
        self.assertEqual(result.message.ar, "يرجى التحقق من وجود لحم الخنزير.")
        self.assertIsNotNone(result.owner_card)

    def test_unknown_menu_keeps_caution_without_hits_or_owner_card(self):
        with patch(
            "ai_result.handlers.unknown_menu_handler.ask_gpt_json",
            return_value={
                "hit_tags": [],
                "message_ko": "DB에 없는 메뉴라 재료 확인이 필요합니다",
                "flag": None,
                "question_ko": None,
            },
        ):
            result = build_final_result(
                {
                    "menu_name_ko": "버터갈릭쉬림프파스타",
                    "risk_level": "caution",
                    "hit_tags": [],
                    "triggered_flags": [],
                    "forbidden_tags": ["is_pork"],
                    "need_gpt": True,
                    "escalation_case": ["unknown_menu"],
                }
            )
        self.assertEqual(result.risk_level, "caution")
        self.assertEqual(result.hits, [])
        self.assertIsNotNone(result.owner_card)
        self.assertEqual(result.owner_card.flag, "unknown_menu")
        self.assertEqual(result.owner_card.question.ko, "이 메뉴에 돼지고기 성분이 들어가나요?")
        self.assertEqual(result.owner_card.question.en, "Does this menu contain pork?")
        self.assertEqual(result.owner_card.question.ar, "هل يحتوي هذا الطبق على لحم الخنزير؟")

    def test_unknown_menu_fallback_owner_card_uses_multiple_forbidden_tags(self):
        with patch(
            "ai_result.handlers.unknown_menu_handler.ask_gpt_json",
            return_value={
                "hit_tags": [],
                "message_ko": "DB에 없는 메뉴라 재료 확인이 필요합니다",
                "flag": None,
                "question_ko": None,
            },
        ):
            result = build_final_result(
                {
                    "menu_name_ko": "셰프특선파스타",
                    "risk_level": "caution",
                    "hit_tags": [],
                    "triggered_flags": [],
                    "forbidden_tags": ["is_pork", "is_alcohol"],
                    "need_gpt": True,
                    "escalation_case": ["unknown_menu"],
                }
            )

        self.assertEqual(result.owner_card.question.ko, "이 메뉴에 돼지고기, 알코올 성분이 들어가나요?")
        self.assertEqual(result.owner_card.question.en, "Does this menu contain pork, alcohol?")
        self.assertEqual(result.owner_card.question.ar, "هل يحتوي هذا الطبق على لحم الخنزير, كحول؟")
