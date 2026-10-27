"""⑧ Decision Policy / XAI Agent adapter matching the Supervisor ``NodeAdapter`` shape."""

from __future__ import annotations

from ai_supervisor.contracts.decision import DecisionRequest, DecisionResponse

from .cards import build_owner_cards
from .messages import build_message
from .policy import decide


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
    """``decision`` node (⓪ §4-7)."""

    async def run(self, request: DecisionRequest) -> DecisionResponse:
        return judge(request)
