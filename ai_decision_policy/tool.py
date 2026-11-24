"""⑧ Decision Policy / XAI Agent adapter matching the Supervisor ``NodeAdapter`` shape."""

from __future__ import annotations

from ai_supervisor.contracts.decision import DecisionRequest, DecisionResponse

from .cards import build_owner_cards
from .llm import XaiLLM
from .messages import build_message, set_meal_text, side_item_text
from .policy import decide
from .reason import ReasonWriter


def judge(request: DecisionRequest) -> DecisionResponse:
    """Verdict, message and owner cards for one menu item (⓪ §4-7)."""

    outcome = decide(request)
    return DecisionResponse(
        context=request.context,
        menu=request.menu,
        risk_level=outcome.risk_level,
        evidence_basis=outcome.evidence_basis,
        information_status=outcome.information_status,
        risk_ingredients=outcome.risk_ingredients,
        matched_tags=outcome.matched_tags,
        evidence_refs=outcome.evidence_refs,
        message=build_message(request, outcome),
        owner_cards=build_owner_cards(request, outcome),
        high_risk_review=outcome.high_risk_review,
        warnings=list(request.warnings),
        errors=list(request.errors),
    )


class DecisionTool:
    """``decision`` node (⓪ §4-7). Rules decide; the LLM, when given, only adds
    ``message.reason`` (§0-2)."""

    def __init__(self, reason_writer: ReasonWriter | None = None) -> None:
        self.reason_writer = reason_writer

    @classmethod
    def from_env(cls) -> "DecisionTool":
        """Rules only when ``OPENAI_API_KEY`` is unset."""

        llm = XaiLLM.from_env_if_configured()
        return cls(ReasonWriter(llm) if llm is not None else None)

    async def run(self, request: DecisionRequest) -> DecisionResponse:
        response = judge(request)
        reason = None
        if self.reason_writer is not None:
            reason = await self.reason_writer.write(request, response)
        # A side item (…사리, …추가) says what it adds first, with or without the LLM (2026-11-24),
        # in place of the menu description (``ReasonWriter`` skips it for a side item).
        # A set meal (백반·정식, 오늘의 ~) says what is unknown about it the same way
        # (2026-11-25), but the menu description stays.
        # One space between sentences: the result screen shows the reason as one paragraph.
        side = side_item_text(request) or set_meal_text(request)
        if side is not None:
            reason = side if reason is None else f"{side} {reason}"
        if reason is None:
            return response
        return response.model_copy(
            update={"message": response.message.model_copy(update={"reason": reason})}
        )
