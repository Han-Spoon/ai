"""Canonical ③ Exact Feedback Tool request and response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from ._validation import require_item_context
from .common import (
    Confirmation,
    FlagConfirmation,
    NodeContext,
    NodeError,
    NodeWarning,
    NonEmptyString,
    PositiveInt,
    StrictModel,
)


class ExactMenu(StrictModel):
    menu_id: PositiveInt
    menu_category_id: PositiveInt | None
    normalized_menu_name: NonEmptyString


class ExactFeedbackRequest(StrictModel):
    context: NodeContext
    menu: ExactMenu
    scope_hint: Literal["exact"]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_context(self) -> "ExactFeedbackRequest":
        require_item_context(self.context, require_store=True)
        return self


class ExactFeedbackResponse(StrictModel):
    context: NodeContext
    menu_id: PositiveInt
    confirmation_scope: Literal["exact"]
    completeness: Literal["complete", "unknown"]
    confirmations: list[Confirmation]
    flag_confirmations: list[FlagConfirmation]
    warnings: list[NodeWarning]
    errors: list[NodeError]

    @model_validator(mode="after")
    def validate_context(self) -> "ExactFeedbackResponse":
        require_item_context(self.context, require_store=True)
        return self
