"""⑤ Bayesian Tool adapter matching the Supervisor ``NodeAdapter`` shape."""

from __future__ import annotations

from ai_supervisor.contracts.bayesian import BayesianRequest, BayesianResponse

from .estimate import estimate_probabilities


class BayesianTool:
    """``bayesian`` node (⓪ §4-4)."""

    async def run(self, request: BayesianRequest) -> BayesianResponse:
        return estimate_probabilities(request)
