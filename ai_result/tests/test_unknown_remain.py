import unittest
from unittest.mock import patch

from ai_result.main import build_final_result


class UnknownRemainHandlerTest(unittest.TestCase):
    def test_unknown_remain_keeps_caution_without_hits(self):
        with patch(
            "ai_result.handlers.unknown_remain_handler.ask_gpt_json",
            return_value={
                "hit_tags": [],
                "message_ko": "추가 재료 확인이 필요합니다",
                "flag": "unknown_remain",
                "question_ko": "셰프 특선 재료에는 무엇이 들어가나요?",
            },
        ):
            result = build_final_result(
                {
                    "menu_name_ko": "셰프특선비빔밥",
                    "risk_level": "caution",
                    "hit_tags": [],
                    "triggered_flags": [],
                    "forbidden_tags": ["is_pork"],
                    "need_gpt": True,
                    "escalation_case": ["unknown_remain"],
                }
            )
        self.assertEqual(result.risk_level, "caution")
        self.assertEqual(result.hits, [])
        self.assertIsNotNone(result.owner_card)

    def test_unknown_remain_fallback_owner_card_when_gpt_question_missing(self):
        with patch(
            "ai_result.handlers.unknown_remain_handler.ask_gpt_json",
            return_value={
                "hit_tags": [],
                "message_ko": "추가 재료 확인이 필요합니다",
                "flag": None,
                "question_ko": None,
            },
        ):
            result = build_final_result(
                {
                    "menu_name_ko": "셰프특선비빔밥",
                    "risk_level": "caution",
                    "hit_tags": [],
                    "triggered_flags": [],
                    "forbidden_tags": ["is_pork"],
                    "need_gpt": True,
                    "escalation_case": ["unknown_remain"],
                }
            )

        self.assertEqual(result.risk_level, "caution")
        self.assertEqual(result.hits, [])
        self.assertIsNotNone(result.owner_card)
        self.assertEqual(result.owner_card.flag, "unknown_remain")
        self.assertEqual(
            result.owner_card.question.ko,
            "셰프특선비빔밥에 어떤 특별한 재료가 들어가나요?",
        )
