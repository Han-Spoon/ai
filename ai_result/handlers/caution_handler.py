from ai_result.core.template_builder import build_final_output, build_owner_card
from ai_result.gpt.gpt_client import ask_gpt_json
from ai_result.gpt.prompts.caution_verify import build_caution_verify_prompt
from ai_result.gpt.response_parser import parse_caution_response
from ai_result.models.final_output import FinalOutput
from ai_result.models.rule_engine_input import RuleEngineInput
from ai_result.rules.hidden_rules import lookup_hidden_candidates, lookup_hidden_candidates_for_context


def handle_caution(rule_input: RuleEngineInput) -> FinalOutput:
    hidden_candidates = lookup_hidden_candidates(
        menu_name=rule_input.menu_name_ko,
        triggered_flags=rule_input.triggered_flags,
    )
    if rule_input.gpt_context:
        hidden_candidates.extend(
            lookup_hidden_candidates_for_context(
                ingredients=rule_input.gpt_context.ingredients_explicit,
                triggered_flags=rule_input.triggered_flags,
            )
        )

    if not hidden_candidates and not rule_input.need_gpt:
        return build_final_output(
            menu_name=rule_input.menu_name_ko,
            risk_level="safe",
            hits=[],
        )

    prompt = build_caution_verify_prompt(rule_input, hidden_candidates)
    parsed = parse_caution_response(ask_gpt_json(prompt))
    hits = [tag for tag in parsed.hit_tags if tag in rule_input.forbidden_tags]

    owner_card = None
    if hits:
        owner_card = build_owner_card(
            menu_name=rule_input.menu_name_ko,
            flag=parsed.flag or (rule_input.triggered_flags[0] if rule_input.triggered_flags else None),
            question_ko=parsed.question_ko,
            question_en=parsed.question_en,
            question_ar=parsed.question_ar,
        )

    return build_final_output(
        menu_name=rule_input.menu_name_ko,
        risk_level="caution",
        hits=hits,
        message_ko=parsed.message_ko,
        owner_card=owner_card,
    )
