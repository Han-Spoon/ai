import unittest

from ai_result.main import build_final_result


class CautionHandlerTest(unittest.TestCase):
    def test_caution_with_no_hidden_rule_hits_becomes_safe(self):
        result = build_final_result(
            {
                "menu_name_ko": "콩나물국",
                "risk_level": "caution",
                "hit_tags": [],
                "triggered_flags": [],
                "forbidden_tags": ["is_milk"],
                "need_gpt": False,
                "escalation_case": [],
            }
        )

        self.assertEqual(result.risk_level, "safe")
        self.assertEqual(result.hits, [])
        self.assertIsNone(result.owner_card)

    def test_caution_with_hidden_rule_hits_keeps_caution_and_owner_card(self):
        result = build_final_result(
            {
                "menu_name_ko": "김치찌개",
                "risk_level": "caution",
                "hit_tags": [],
                "triggered_flags": ["has_unclear_jeotgal"],
                "forbidden_tags": ["is_fish"],
                "need_gpt": True,
                "escalation_case": [],
                "gpt_context": {
                    "base_menu": "김치찌개",
                    "ingredients_explicit": ["배추김치", "김칫국물"],
                    "explicit_tags": [],
                    "variant_tags": [],
                },
            }
        )

        self.assertEqual(result.risk_level, "caution")
        self.assertEqual(result.hits, ["is_fish"])
        self.assertIsNotNone(result.owner_card)
        self.assertEqual(result.owner_card.flag, "has_unclear_jeotgal")

    def test_doenjang_jjigae_broth_flag_hits_fish(self):
        result = build_final_result(
            {
                "menu_name_ko": "된장찌개",
                "is_spicy": False,
                "risk_level": "caution",
                "hit_tags": [],
                "triggered_flags": ["has_unclear_broth", "has_unclear_jeotgal"],
                "forbidden_tags": ["is_fish"],
                "need_gpt": True,
                "escalation_case": ["ambiguity"],
                "gpt_context": {
                    "base_menu": "된장찌개",
                    "ingredients_explicit": ["된장", "두부", "애호박", "감자", "양파", "대파", "마늘", "고춧가루"],
                    "explicit_tags": ["is_soybean"],
                    "variant_tags": [],
                },
            }
        )

        self.assertEqual(result.risk_level, "caution")
        self.assertEqual(result.hits, ["is_fish"])
        self.assertIsNotNone(result.owner_card)
        self.assertEqual(result.owner_card.flag, "has_unclear_broth")
