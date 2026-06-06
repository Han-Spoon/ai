import json

from ai_result.models.input_verification import RuleEngineInput


SYSTEM = """
You create an owner confirmation card for an unrecognized menu variant token.
Return JSON only using this schema:
{
  "hit_tags": [],
  "message_ko": "추가 재료 확인이 필요합니다",
  "message_en": "Additional ingredients need to be checked.",
  "message_ar": "يجب التحقق من المكونات الإضافية.",
  "flag": "unknown_remain",
  "question_ko": "셰프 특선 재료에는 무엇이 들어가나요?",
  "question_en": "What special ingredients are used in this variant?",
  "question_ar": "ما المكونات الخاصة المستخدمة في هذا النوع؟"
}
Do not mark the result safe just because hit_tags is empty.
Generate a specific question about the unrecognized variant token.
All fields are required. Do not omit any field.

Example:
menu_name="셰프특선비빔밥", remain_tokens=["셰프특선"]
question_ko="셰프 특선 비빔밥에는 어떤 셰프 특선 재료가 들어가나요?"
"""


def build_unknown_remain_prompt(rule_input: RuleEngineInput) -> dict:
    payload = {
        "task": "unknown_remain_owner_card",
        "menu_name": rule_input.menu_name_ko,
        "forbidden_tags": rule_input.forbidden_tags,
        "gpt_context": rule_input.gpt_context.model_dump() if rule_input.gpt_context else None,
        "escalation_case": rule_input.escalation_case,
        "remain_tokens": rule_input.gpt_context.variant_tags if rule_input.gpt_context else [],
    }
    return {"system": SYSTEM, "user": json.dumps(payload, ensure_ascii=False)}
