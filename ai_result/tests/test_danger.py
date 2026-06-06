import unittest

from ai_result.main import build_final_result


class DangerHandlerTest(unittest.TestCase):
    def test_danger_uses_existing_hits(self):
        result = build_final_result(
            {
                "menu_name_ko": "김치찌개",
                "risk_level": "danger",
                "hit_tags": ["is_fish"],
                "triggered_flags": [],
                "forbidden_tags": ["is_fish"],
                "need_gpt": False,
                "escalation_case": [],
            }
        )
        self.assertEqual(result.risk_level, "danger")
        self.assertEqual(result.hits, ["is_fish"])
        self.assertIsNone(result.owner_card)
