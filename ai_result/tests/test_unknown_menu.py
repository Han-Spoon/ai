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
        self.assertIsNotNone(result.owner_card)
