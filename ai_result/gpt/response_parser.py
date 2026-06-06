from pydantic import BaseModel


class ParsedGptResult(BaseModel):
    hit_tags: list[str] = []
    message_ko: str | None = None
    flag: str | None = None
    question_ko: str | None = None
    question_en: str | None = None
    question_ar: str | None = None


def parse_caution_response(raw: dict) -> ParsedGptResult:
    return ParsedGptResult(**raw)


def parse_unknown_menu_response(raw: dict) -> ParsedGptResult:
    return ParsedGptResult(**raw)


def parse_unknown_remain_response(raw: dict) -> ParsedGptResult:
    return ParsedGptResult(**raw)
