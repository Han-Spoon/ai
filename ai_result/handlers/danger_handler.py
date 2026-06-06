from ai_result.core.template_builder import build_final_output
from ai_result.models.final_output import FinalOutput
from ai_result.models.rule_engine_input import RuleEngineInput


def handle_danger(rule_input: RuleEngineInput) -> FinalOutput:
    hits = rule_input.hit_tags
    return build_final_output(
        menu_name=rule_input.menu_name_ko,
        risk_level="danger",
        hits=hits,
        message_ko=f"{', '.join(hits)} 성분이 포함되어 있어 섭취에 주의가 필요합니다.",
    )
