import unittest
from unittest.mock import patch

from ai_result.main import build_final_result


class CaseRouterTest(unittest.TestCase):
    def test_safe_does_not_call_caution_handler(self):
        with patch("ai_result.core.case_router.handle_caution") as handle_caution:
            result = build_final_result(
                {
                    "menu_name_ko": "삼겹살",
                    "risk_level": "safe",
                    "hit_tags": [],
                    "triggered_flags": [],
                    "forbidden_tags": [],
                    "need_gpt": False,
                    "escalation_case": [],
                }
            )

        handle_caution.assert_not_called()
        self.assertEqual(result.risk_level, "safe")
        self.assertEqual(result.hits, [])

    def test_danger_has_priority_over_escalation_case(self):
        with patch("ai_result.core.case_router.handle_unknown_menu") as handle_unknown_menu:
            result = build_final_result(
                {
                    "menu_name_ko": "삼겹살",
                    "risk_level": "danger",
                    "hit_tags": ["is_pork"],
                    "triggered_flags": [],
                    "forbidden_tags": ["is_pork"],
                    "need_gpt": False,
                    "escalation_case": ["unknown_menu"],
                }
            )

        handle_unknown_menu.assert_not_called()
        self.assertEqual(result.risk_level, "danger")
        self.assertEqual(result.hits, ["is_pork"])
