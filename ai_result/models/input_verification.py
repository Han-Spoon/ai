from typing import Any, Literal

from pydantic import BaseModel


class GptContext(BaseModel):
    base_menu: str | None = None
    ingredients_explicit: list[str] = []
    explicit_tags: list[str] = []
    variant_tags: list[str] = []


class RuleEngineInput(BaseModel):
    menu_name_ko: str
    is_spicy: bool | None = None
    risk_level: Literal["danger", "caution", "safe"]
    hit_tags: list[str] = []
    triggered_flags: list[str] = []
    forbidden_tags: list[str] = []
    need_gpt: bool = False
    escalation_case: list[str] = []
    gpt_context: GptContext | None = None
    risk_reasons: list[dict[str, Any]] | None = None
