from ai_result.core.message_builder import build_message, build_tag_names
from ai_result.core.template_builder import build_final_output, build_owner_card
from ai_result.models.final_output import FinalOutput
from ai_result.models.input_verification import RuleEngineInput
from ai_result.rules.hidden_rules import HiddenCandidate, lookup_hidden_candidates, lookup_hidden_candidates_for_context


def _collect_hidden_candidates(rule_input: RuleEngineInput) -> list[HiddenCandidate]:
    # 음식명과 애매함 플래그를 기준으로, 의심 가능한 hidden rule 후보를 먼저 찾는다.
    hidden_candidates = lookup_hidden_candidates(
        menu_name=rule_input.menu_name_ko,
        triggered_flags=rule_input.triggered_flags,
    )

    # 룰엔진이 넘겨준 명시 재료가 있으면, 재료명 기준 hidden rule 후보도 추가로 찾는다.
    if rule_input.gpt_context:
        hidden_candidates.extend(
            lookup_hidden_candidates_for_context(
                ingredients=rule_input.gpt_context.ingredients_explicit,
                triggered_flags=rule_input.triggered_flags,
            )
        )

    return hidden_candidates


def _filter_forbidden_hits(
    hidden_candidates: list[HiddenCandidate],
    forbidden_tags: list[str],
) -> tuple[list[str], list[HiddenCandidate]]:
    hits = []
    relevant_candidates = []

    for candidate in hidden_candidates:
        candidate_hits = [tag for tag in candidate.tags if tag in forbidden_tags]
        if not candidate_hits:
            continue

        relevant_candidates.append(candidate)
        hits.extend(candidate_hits)

    return list(dict.fromkeys(hits)), relevant_candidates


def _build_caution_owner_card(
    rule_input: RuleEngineInput,
    hits: list[str],
    relevant_candidates: list[HiddenCandidate],
):
    flag = (
        relevant_candidates[0].flag
        if relevant_candidates and relevant_candidates[0].flag
        else rule_input.triggered_flags[0] if rule_input.triggered_flags else "hidden_rule"
    )
    hidden_names = []
    for candidate in relevant_candidates:
        hidden_names.extend(candidate.hidden)

    hidden_ko = ", ".join(dict.fromkeys(hidden_names))
    hit_names_en = build_tag_names(hits, "en")
    hit_names_ar = build_tag_names(hits, "ar")

    return build_owner_card(
        menu_name=rule_input.menu_name_ko,
        flag=flag,
        question_ko=(
            f"이 메뉴에 {hidden_ko} 성분이 들어가나요?"
            if hidden_ko
            else "이 메뉴에 제한 성분이 들어가나요?"
        ),
        question_en=(
            f"Does this menu contain {hit_names_en}?"
            if hit_names_en
            else "Does this menu contain restricted ingredients?"
        ),
        question_ar=(
            f"هل يحتوي هذا الطبق على {hit_names_ar}؟"
            if hit_names_ar
            else "هل يحتوي هذا الطبق على مكونات مقيدة؟"
        ),
    )


def handle_caution(rule_input: RuleEngineInput) -> FinalOutput:
    hidden_candidates = _collect_hidden_candidates(rule_input)
    hits, relevant_candidates = _filter_forbidden_hits(
        hidden_candidates=hidden_candidates,
        forbidden_tags=rule_input.forbidden_tags,
    )

    # hidden rule 후보가 없거나, 후보가 사용자 제한 태그와 겹치지 않으면 safe로 전환한다.
    if not hits:
        return build_final_output(
            menu_name=rule_input.menu_name_ko,
            risk_level="safe",
            hits=[],
            message=build_message([], "safe"),
        )

    # 최종 hits가 있으면 caution을 유지하고, hidden rule 기반 owner_card를 포함해 반환한다.
    return build_final_output(
        menu_name=rule_input.menu_name_ko,
        risk_level="caution",
        hits=hits,
        message=build_message(hits, "caution"),
        owner_card=_build_caution_owner_card(rule_input, hits, relevant_candidates),
    )
