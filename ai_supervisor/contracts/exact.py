"""Canonical ③ Exact Feedback Tool request and response contracts."""

from __future__ import annotations

from typing import Any, Literal

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
    OwnerQuestionState,
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

    @model_validator(mode="before")
    @classmethod
    def discard_empty_legacy_question_states(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "owner_question_states" not in value:
            return value
        if value["owner_question_states"] != []:
            raise ValueError("multi-round owner question state is no longer supported")
        cleaned = dict(value)
        cleaned.pop("owner_question_states")
        return cleaned

    @property
    def owner_question_states(self) -> list[OwnerQuestionState]:
        """Compatibility view for old internal adapters; never serialized."""

        return []

    @model_validator(mode="after")
    def validate_context(self) -> "ExactFeedbackResponse":
        require_item_context(self.context, require_store=True)
        return self
