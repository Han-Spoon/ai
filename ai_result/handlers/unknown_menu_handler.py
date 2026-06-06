from ai_result.core.message_builder import build_message, build_tag_names
from ai_result.core.template_builder import build_final_output, build_owner_card
from ai_result.gpt.gpt_client import ask_gpt_json
from ai_result.gpt.prompts.unknown_menu import build_unknown_menu_prompt
from ai_result.gpt.response_parser import parse_unknown_menu_response
from ai_result.models.final_output import FinalOutput
from ai_result.models.input_verification import RuleEngineInput


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
    if not owner_card:
        forbidden_ko = build_tag_names(rule_input.forbidden_tags, "ko")
        forbidden_en = build_tag_names(rule_input.forbidden_tags, "en")
        forbidden_ar = build_tag_names(rule_input.forbidden_tags, "ar")

        owner_card = build_owner_card(
            menu_name=rule_input.menu_name_ko,
            flag="unknown_menu",
            question_ko=(
                f"이 메뉴에 {forbidden_ko} 성분이 들어가나요?"
                if forbidden_ko
                else "이 메뉴에 어떤 재료가 들어가나요?"
            ),
            question_en=(
                f"Does this menu contain {forbidden_en}?"
                if forbidden_en
                else "What ingredients are in this menu?"
            ),
            question_ar=(
                f"هل يحتوي هذا الطبق على {forbidden_ar}؟"
                if forbidden_ar
                else "ما هي المكونات في هذا الطبق؟"
            ),
        )

    return build_final_output(
        menu_name=rule_input.menu_name_ko,
        risk_level="caution",
        hits=hits,
        message=build_message(
            hits,
            "caution",
            ko_override=parsed.message_ko,
            en_override=parsed.message_en,
            ar_override=parsed.message_ar,
        ),
        owner_card=owner_card,
    )
