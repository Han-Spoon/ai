from typing import Literal

from pydantic import BaseModel


class FinalMessage(BaseModel):
    ko: str
    en: str | None = None
    ar: str | None = None


class OwnerQuestion(BaseModel):
    ko: str
    en: str | None = None
    ar: str | None = None


class OwnerCard(BaseModel):
    menu_name: str
    flag: str
    question: OwnerQuestion


class FinalOutput(BaseModel):
    menu_name: str
    risk_level: Literal["danger", "caution", "safe"]
    hits: list[str]
    message: FinalMessage
    owner_card: OwnerCard | None = None
