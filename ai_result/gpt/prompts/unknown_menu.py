import json

from ai_result.models.input_verification import RuleEngineInput


SYSTEM = """
You analyze an unknown menu that is not matched in the Korean food DB.
Return JSON only using this schema:
{
  "hit_tags": [],
  "message_ko": "확인이 필요한 재료가 있을 수 있습니다",
  "message_en": "Some ingredients may need to be checked.",
  "message_ar": "قد تكون هناك مكونات تحتاج إلى التحقق.",
  "flag": "unknown_menu",
  "question_ko": "이 메뉴에 제한 성분이 들어가나요?",
  "question_en": "Does this menu contain restricted ingredients?",
  "question_ar": "هل يحتوي هذا الطبق على مكونات مقيدة؟"
}
Analyze the menu name and infer which of the forbidden_tags might be present.
Return only tags that are clearly likely based on the menu name.
If uncertain, return hit_tags as [].
Do not create tags outside forbidden_tags.
All fields are required. Do not omit any field.

Generate a specific owner question based on the menu name and forbidden_tags.
Example:
menu_name="버터갈릭쉬림프파스타", forbidden_tags=["is_pork"]
question_ko="이 파스타에 베이컨이나 햄이 들어가나요?"
"""


def build_unknown_menu_prompt(rule_input: RuleEngineInput) -> dict:
    payload = {
        "task": "unknown_menu_template",
        "menu_name": rule_input.menu_name_ko,
        "forbidden_tags": rule_input.forbidden_tags,
        "gpt_context": rule_input.gpt_context.model_dump() if rule_input.gpt_context else None,
    }
    return {"system": SYSTEM, "user": json.dumps(payload, ensure_ascii=False)}
