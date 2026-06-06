# risk_level과 escalation_case를 보고 어느 handler로 보낼지 결정하는 분기 로직

from ai_result.core.template_builder import build_final_output
from ai_result.handlers.caution_handler import handle_caution
from ai_result.handlers.danger_handler import handle_danger
from ai_result.handlers.unknown_menu_handler import handle_unknown_menu
from ai_result.handlers.unknown_remain_handler import handle_unknown_remain
from ai_result.models.final_output import FinalOutput
from ai_result.models.rule_engine_input import RuleEngineInput


def route_case(rule_input: RuleEngineInput) -> FinalOutput:
    # danger는 이미 hit_tags가 확정된 상태이므로 에스컬레이션보다 먼저 처리한다.
    if rule_input.risk_level == "danger":
        return handle_danger(rule_input)

    # safe는 GPT 검증이나 owner_card 생성 없이 그대로 안전 템플릿을 반환한다.
    if rule_input.risk_level == "safe":
        return build_final_output(
            menu_name=rule_input.menu_name_ko,
            risk_level="safe",
            hits=[],
        )

    if "unknown_menu" in rule_input.escalation_case:
        return handle_unknown_menu(rule_input)

    if "unknown_remain" in rule_input.escalation_case:
        return handle_unknown_remain(rule_input)

    if rule_input.risk_level == "caution":
        return handle_caution(rule_input)

    return build_final_output(
        menu_name=rule_input.menu_name_ko,
        risk_level="safe",
        hits=[],
    )
