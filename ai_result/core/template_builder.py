# 최종 JSON 템플릿 조립

from ai_result.models.final_output import FinalMessage, FinalOutput, OwnerCard, OwnerQuestion


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
    message: FinalMessage | None = None,
    owner_card: OwnerCard | None = None,
) -> FinalOutput:
    return FinalOutput(
        menu_name=menu_name,
        risk_level=risk_level,
        hits=hits,
        message=message,
        owner_card=owner_card,
    )
