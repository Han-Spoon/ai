import unittest

from ai_result.core.message_builder import build_message
from ai_result.core.template_builder import build_final_output


class TemplateBuilderTest(unittest.TestCase):
    def test_safe_message_has_all_languages(self):
        result = build_final_output(
            menu_name="비빔밥",
            risk_level="safe",
            hits=[],
            message=build_message([], "safe"),
        )

        self.assertEqual(result.message.ko, "안전하게 드실 수 있어요.")
        self.assertEqual(result.message.en, "This menu is safe for you.")
        self.assertEqual(result.message.ar, "هذا الطبق آمن لك.")

    def test_hit_tags_message_has_all_languages(self):
        result = build_final_output(
            menu_name="된장찌개",
            risk_level="caution",
            hits=["is_fish", "is_shellfish"],
            message=build_message(["is_fish", "is_shellfish"], "caution"),
        )

        self.assertEqual(
            result.message.ko,
            "생선, 조개류 성분이 포함되어 있을 수 있어요.",
        )
        self.assertEqual(
            result.message.en,
            "This menu may contain fish, shellfish.",
        )
        self.assertEqual(
            result.message.ar,
            "قد يحتوي هذا الطبق على سمك, المحار.",
        )

    def test_danger_message_uses_confirmed_wording(self):
        result = build_final_output(
            menu_name="삼겹살",
            risk_level="danger",
            hits=["is_pork"],
            message=build_message(["is_pork"], "danger"),
        )

        self.assertEqual(result.message.ko, "돼지고기 성분이 포함되어 있어요.")
        self.assertEqual(result.message.en, "This menu contains pork.")
        self.assertEqual(result.message.ar, "يحتوي هذا الطبق على لحم الخنزير.")
