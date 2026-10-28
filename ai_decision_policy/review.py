"""⑧ high-risk review: one more LLM look at a short-of-information or conflicting menu
(docs/8-agent-xai.md §2-3, ⓪ §4-7 ``HighRiskReviewRequest``/``Response``).

The Supervisor picks at most three menus per scan and calls this after every verdict is
in. The review never changes a verdict. It can only

- name likely ingredients the menu list missed, chosen from the standard ingredient
  list, and ask the owner about each one that hits the user's restrictions,
- rewrite the reason sentence, which code checks the same way as §4 (#174).

Any LLM failure, timeout or bad output gives back the original cards and reason.
"""

from __future__ import annotations

import asyncio
import re

from pydantic import BaseModel, Field

from ai_supervisor.contracts.common import IngredientRef
from ai_supervisor.contracts.decision import HighRiskReviewRequest, HighRiskReviewResponse
from ai_supervisor.contracts.external import OwnerCard

from .llm import XaiLLM
from .profile import forbidden_tags
from .questions import ingredient_question
from .reason import LANGUAGE_NAMES, check_reason, with_translations

MAX_OUTPUT_TOKENS = 600
_NUMBER_RE = re.compile(r"\d+")

INSTRUCTIONS = """You double-check one Korean restaurant menu for a tourist with food restrictions.
Code already decided the verdict; you must not change it.
1. missed_ingredient_ids: ids from ingredient_choices that this dish very often
   contains but found_ingredients does not list. Only common ones, never rare
   additions. Use an empty list when nothing is commonly missed.
2. reason: one or two short, friendly sentences in {language}, speaking to the
   tourist, about why this dish needs care for them. A missed ingredient may only be
   described as a possibility. Use only ingredients from the input, with the given
   names (localized_name for choices). Copy any percentage from verdict_sentence
   exactly; never write 0% or 100%. Never say or imply the dish is safe.
   Write every word in {language}. Call the dish "this dish" in {language}, not by
   its Korean name.
   Do not mention the input, data, codes, or field names, and do not repeat
   verdict_sentence. In Korean, end sentences with -요."""


class ReviewOutput(BaseModel):
    missed_ingredient_ids: list[int] = Field(default_factory=list)
    reason: str


def _card_id(item_id: str, ingredient_id: int) -> str:
    # Same id the decision cards use (cards.py), so one ingredient never gets two cards.
    return f"card:{item_id}:ingredient-{ingredient_id}"


def _payload(request: HighRiskReviewRequest, choices: list, forbidden: frozenset[str]) -> dict:
    return {
        "menu": {
            "name": request.menu.normalized_menu_name,
            "as_written": request.menu.raw_menu_name,
            "description": request.menu.description,
        },
        "review_reason": request.reason_code,
        "verdict_code": {
            "risk_level": request.decision.risk_level,
            "evidence_basis": request.decision.evidence_basis,
        },
        "verdict_sentence": request.decision.message.verdict,
        "current_reason": request.decision.message.reason,
        "user_restrictions": sorted(forbidden),
        "found_ingredients": [
            {"name": item.ingredient.canonical_name, "tags": item.constraint_tags}
            for item in request.found_ingredients
        ],
        "ingredient_choices": [
            {
                "id": choice.ingredient_id,
                "name": choice.canonical_name,
                "localized_name": choice.localized_name,
                "tags": choice.constraint_tags,
            }
            for choice in choices
        ],
    }


class HighRiskReviewAgent:
    """``high_risk_review`` node (⓪ §4-7). Never raises."""

    def __init__(self, llm: XaiLLM | None, *, timeout_seconds: float = 5.0) -> None:
        self.llm = llm
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "HighRiskReviewAgent":
        """With ``OPENAI_API_KEY`` unset the agent still answers, with no changes."""

        return cls(XaiLLM.from_env_if_configured())

    def _unchanged(self, request: HighRiskReviewRequest) -> HighRiskReviewResponse:
        return HighRiskReviewResponse(
            context=request.context,
            missed_ingredients=[],
            reason=request.decision.message.reason,
            owner_cards=list(request.decision.owner_cards),
            warnings=list(request.warnings),
            errors=list(request.errors),
        )

    async def run(self, request: HighRiskReviewRequest) -> HighRiskReviewResponse:
        if self.llm is None:
            return self._unchanged(request)

        forbidden = forbidden_tags(request.user_profile)
        # Only ingredients that can hit this user are worth naming: the rest would not
        # make a card or change the explanation, and the prompt stays small.
        choices = [c for c in request.ingredient_choices if set(c.constraint_tags) & forbidden]
        try:
            output = await asyncio.wait_for(
                self.llm.parse(
                    INSTRUCTIONS.format(language=LANGUAGE_NAMES[request.locale]),
                    _payload(request, choices, forbidden),
                    ReviewOutput,
                    MAX_OUTPUT_TOKENS,
                ),
                timeout=self.timeout_seconds,
            )
        except Exception:
            return self._unchanged(request)

        item_id = request.context.item_id
        by_id = {c.ingredient_id: c for c in choices}
        asked = {card.card_id for card in request.decision.owner_cards}
        missed = [
            by_id[i]
            for i in dict.fromkeys(output.missed_ingredient_ids)
            if i in by_id and _card_id(item_id, i) not in asked
        ]
        new_cards = [
            OwnerCard(
                item_id=item_id,
                card_id=_card_id(item_id, choice.ingredient_id),
                menu_id=request.menu.menu_id,
                round=1,
                previous_question_id=None,
                ingredient=IngredientRef(
                    ingredient_id=choice.ingredient_id, canonical_name=choice.canonical_name
                ),
                ambiguity_flag=None,
                constraint_tags=[],
                question=ingredient_question(
                    choice.canonical_name, choice.localized_name, request.locale
                ),
                reason_codes=["high_risk_review"],
                evidence_refs=[],
            )
            for choice in missed
        ]

        message = request.decision.message
        names = with_translations(i.ingredient.canonical_name for i in request.found_ingredients)
        names |= {n for c in missed for n in (c.canonical_name, c.localized_name)}
        shown = [int(n) for n in _NUMBER_RE.findall(message.verdict)]
        reason = check_reason(
            output.reason,
            locale=request.locale,
            risk_level=request.decision.risk_level,
            percents=shown,
            allowed_names=names,
            menu_names=[request.menu.normalized_menu_name, request.menu.raw_menu_name],
            verdict=message.verdict,
        )
        return HighRiskReviewResponse(
            context=request.context,
            missed_ingredients=[
                IngredientRef(ingredient_id=c.ingredient_id, canonical_name=c.canonical_name)
                for c in missed
            ],
            reason=reason if reason is not None else message.reason,
            owner_cards=[*request.decision.owner_cards, *new_cards],
            warnings=list(request.warnings),
            errors=list(request.errors),
        )
