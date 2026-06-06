import json

from ai_result.models.rule_engine_input import RuleEngineInput


SYSTEM = """
You verify ambiguous Korean menu allergy and restriction risks.
Return JSON only.
Schema:
{
  "hit_tags": ["is_fish"],
  "message_ko": "어류 성분이 포함되어 있을 가능성이 있습니다",
  "flag": "has_unclear_jeotgal",
  "question_ko": "혹시 새우젓이나 멸치젓을 사용하시나요?",
  "question_en": "Do you use salted shrimp or anchovy jeotgal?",
  "question_ar": "هل تستخدم معجون الروبيان المملح أو الأنشوجة؟"
}
Only include hit_tags that are plausible from the supplied hidden candidates and user forbidden tags.
"""


def build_caution_verify_prompt(rule_input: RuleEngineInput, hidden_candidates: list) -> dict:
    payload = {
        "task": "verify_caution_hidden_risk",
        "menu_name": rule_input.menu_name_ko,
        "triggered_flags": rule_input.triggered_flags,
        "forbidden_tags": rule_input.forbidden_tags,
        "hidden_candidates": [candidate.model_dump() for candidate in hidden_candidates],
        "gpt_context": rule_input.gpt_context.model_dump() if rule_input.gpt_context else None,
    }
    return {"system": SYSTEM, "user": json.dumps(payload, ensure_ascii=False)}
