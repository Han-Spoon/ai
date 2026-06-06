import json

from ai_result.models.rule_engine_input import RuleEngineInput


SYSTEM = """
You analyze an unknown menu that is not matched in the Korean food DB.
Return JSON only using this schema:
{
  "hit_tags": [],
  "message_ko": "확인이 필요한 재료가 있을 수 있습니다",
  "flag": "unknown_menu",
  "question_ko": "이 메뉴에 어떤 재료가 들어가나요?",
  "question_en": "What ingredients are used in this menu?",
  "question_ar": "ما المكونات المستخدمة في هذا الطبق؟"
}
Use the menu name and forbidden tags to infer likely risk tags conservatively.
"""


def build_unknown_menu_prompt(rule_input: RuleEngineInput) -> dict:
    payload = {
        "task": "unknown_menu_template",
        "menu_name": rule_input.menu_name_ko,
        "forbidden_tags": rule_input.forbidden_tags,
        "gpt_context": rule_input.gpt_context.model_dump() if rule_input.gpt_context else None,
    }
    return {"system": SYSTEM, "user": json.dumps(payload, ensure_ascii=False)}
