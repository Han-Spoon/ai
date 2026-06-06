#최종 json템플릿 조립

from ai_result.models.final_output import FinalMessage, FinalOutput, OwnerCard, OwnerQuestion


def build_message_ko(hit_tags: list[str], risk_level: str) -> str:
    if risk_level == "safe":
        return "입력한 제한 항목 기준으로는 안전한 메뉴로 판단됩니다."

    if not hit_tags:
        return "확인이 필요한 재료가 있을 수 있습니다."

    names = ", ".join(hit_tags)
    return f"{names} 성분이 포함되어 있을 가능성이 있습니다."


def build_owner_card(
    menu_name: str,
    flag: str | None,
    question_ko: str | None,
    question_en: str | None = None,
    question_ar: str | None = None,
) -> OwnerCard | None:
    if not flag or not question_ko:
        return None

    return OwnerCard(
        menu_name=menu_name,
        flag=flag,
        question=OwnerQuestion(
            ko=question_ko,
            en=question_en,
            ar=question_ar,
        ),
    )


def build_final_output(
    menu_name: str,
    risk_level: str,
    hits: list[str],
    message_ko: str | None = None,
    owner_card: OwnerCard | None = None,
) -> FinalOutput:
    final_risk_level = "safe" if risk_level == "caution" and not hits and owner_card is None else risk_level
    return FinalOutput(
        menu_name=menu_name,
        risk_level=final_risk_level,
        hits=hits,
        message=FinalMessage(
            ko=message_ko or build_message_ko(hits, final_risk_level),
            en=None,
            ar=None,
        ),
        owner_card=owner_card,
    )
