from ai_result.core.template_builder import build_final_output, build_owner_card
from ai_result.gpt.gpt_client import ask_gpt_json
from ai_result.gpt.prompts.unknown_remain import build_unknown_remain_prompt
from ai_result.gpt.response_parser import parse_unknown_remain_response
from ai_result.models.final_output import FinalOutput
from ai_result.models.rule_engine_input import RuleEngineInput


def handle_unknown_remain(rule_input: RuleEngineInput) -> FinalOutput:
    parsed = parse_unknown_remain_response(
        ask_gpt_json(build_unknown_remain_prompt(rule_input))
    )
    owner_card = build_owner_card(
        menu_name=rule_input.menu_name_ko,
        flag=parsed.flag or "unknown_remain",
        question_ko=parsed.question_ko,
        question_en=parsed.question_en,
        question_ar=parsed.question_ar,
    )
    return build_final_output(
        menu_name=rule_input.menu_name_ko,
        risk_level="caution",
        hits=[],
        message_ko=parsed.message_ko,
        owner_card=owner_card,
    )
