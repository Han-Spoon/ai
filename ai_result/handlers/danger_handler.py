from ai_result.core.message_builder import build_message
from ai_result.core.template_builder import build_final_output
from ai_result.models.final_output import FinalOutput
from ai_result.models.input_verification import RuleEngineInput


def handle_danger(rule_input: RuleEngineInput) -> FinalOutput:
    hits = rule_input.hit_tags
    return build_final_output(
        menu_name=rule_input.menu_name_ko,
        risk_level="danger",
        hits=hits,
        message=build_message(hits, "danger"),
    )
