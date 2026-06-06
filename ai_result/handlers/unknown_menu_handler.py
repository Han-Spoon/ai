from ai_result.core.template_builder import build_final_output, build_owner_card
from ai_result.gpt.gpt_client import ask_gpt_json
from ai_result.gpt.prompts.unknown_menu import build_unknown_menu_prompt
from ai_result.gpt.response_parser import parse_unknown_menu_response
from ai_result.models.final_output import FinalOutput
from ai_result.models.rule_engine_input import RuleEngineInput


def handle_unknown_menu(rule_input: RuleEngineInput) -> FinalOutput:
    parsed = parse_unknown_menu_response(
        ask_gpt_json(build_unknown_menu_prompt(rule_input))
    )
    hits = [tag for tag in parsed.hit_tags if tag in rule_input.forbidden_tags]
    owner_card = build_owner_card(
        menu_name=rule_input.menu_name_ko,
        flag=parsed.flag,
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
