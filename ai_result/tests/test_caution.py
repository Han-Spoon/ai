import unittest
from unittest.mock import patch

from ai_result.main import build_final_result


class CautionHandlerTest(unittest.TestCase):
    def test_caution_with_no_hits_becomes_safe(self):
        with patch("ai_result.handlers.caution_handler.ask_gpt_json", return_value={"hit_tags": []}):
            result = build_final_result(
                {
                    "menu_name_ko": "된장찌개",
                    "risk_level": "caution",
                    "hit_tags": [],
                    "triggered_flags": ["has_unclear_broth"],
                    "forbidden_tags": ["is_milk"],
                    "need_gpt": True,
                    "escalation_case": [],
                }
            )
        self.assertEqual(result.risk_level, "safe")
        self.assertEqual(result.hits, [])


    def test_caution_with_hits_keeps_caution_and_owner_card(self):
        with patch(
            "ai_result.handlers.caution_handler.ask_gpt_json",
            return_value={
                "hit_tags": ["is_fish"],
                "message_ko": "어류 성분이 포함되어 있을 가능성이 있습니다",
                "flag": "has_unclear_jeotgal",
                "question_ko": "혹시 새우젓이나 멸치젓을 사용하시나요?",
            },
        ):
            result = build_final_result(
                {
                    "menu_name_ko": "된장찌개",
                    "risk_level": "caution",
                    "hit_tags": [],
                    "triggered_flags": ["has_unclear_jeotgal"],
                    "forbidden_tags": ["is_fish"],
                    "need_gpt": True,
                    "escalation_case": [],
                }
            )
        self.assertEqual(result.risk_level, "caution")
        self.assertEqual(result.hits, ["is_fish"])
        self.assertIsNotNone(result.owner_card)
