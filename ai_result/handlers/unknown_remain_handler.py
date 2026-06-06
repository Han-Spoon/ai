from ai_result.core.message_builder import build_message
from ai_result.core.template_builder import build_final_output, build_owner_card
from ai_result.gpt.gpt_client import ask_gpt_json
from ai_result.gpt.prompts.unknown_remain import build_unknown_remain_prompt
from ai_result.gpt.response_parser import parse_unknown_remain_response
from ai_result.models.final_output import FinalOutput
from ai_result.models.input_verification import RuleEngineInput


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
    if not owner_card:
        owner_card = build_owner_card(
            menu_name=rule_input.menu_name_ko,
            flag="unknown_remain",
            question_ko=f"{rule_input.menu_name_ko}에 어떤 특별한 재료가 들어가나요?",
            question_en=f"What special ingredients are in {rule_input.menu_name_ko}?",
            question_ar=f"ما هي المكونات الخاصة في {rule_input.menu_name_ko}؟",
        )

    return build_final_output(
        menu_name=rule_input.menu_name_ko,
        risk_level="caution",
        hits=[],
        message=build_message(
            [],
            "caution",
            ko_override=parsed.message_ko,
            en_override=parsed.message_en,
            ar_override=parsed.message_ar,
        ),
        owner_card=owner_card,
    )
