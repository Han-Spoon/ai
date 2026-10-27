"""Application service that compiles and invokes the Supervisor graphs once."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from ai_bayesian import BayesianTool
from ai_db_update import DbUpdateTool
from ai_decision_policy import DecisionTool
from ai_normalization import CandidateSelectionAgent, NameCleaningAgent
from ai_normalization.contracts import (
    CandidateSelectionRequest,
    CandidateSelectionResponse,
    NameCleaningRequest,
    NameCleaningResponse,
)
from ai_ontology import IngredientMappingTool, OntologyTool, TagLookupTool

from .adapters import NodeAdapter
from .contracts.bayesian import BayesianRequest, BayesianResponse
from .contracts.db_update import DbUpdateRequest, DbUpdateResponse
from .contracts.decision import (
    DecisionRequest,
    DecisionResponse,
    HighRiskReviewRequest,
    HighRiskReviewResponse,
)
from .contracts.external import JudgeRequest, JudgeResponse, PrepareRequest, PrepareResponse
from .contracts.ocr import OcrRequest, OcrResponse
from .contracts.ontology import (
    IngredientMappingRequest,
    IngredientMappingResponse,
    OntologyRequest,
    OntologyResponse,
    TagLookupRequest,
    TagLookupResponse,
)
from .contracts.web_search import (
    WebAggregateRequest,
    WebAggregateResponse,
    WebCollectRequest,
    WebCollectResponse,
)
from .item_graph import ItemGraphAnalyzer, ItemGraphDependencies
from .judge_graph import JudgeDependencies, build_judge_graph
from .prepare_graph import PrepareDependencies, build_prepare_graph
from .state import RunContext


@dataclass(frozen=True)
class SupervisorServiceDependencies:
    """Lifespan-scoped adapters used to assemble the two public workflows.

    OCR, web collection, and expensive review need application/provider
    configuration, so callers inject them explicitly. Existing deterministic
    ②/④/⑤/⑥-aggregate/⑦/⑧ implementations are used by default.
    """

    ocr: NodeAdapter[OcrRequest, OcrResponse]
    web_collect: NodeAdapter[WebCollectRequest, WebCollectResponse]
    high_risk_review: NodeAdapter[HighRiskReviewRequest, HighRiskReviewResponse]
    decision: NodeAdapter[DecisionRequest, DecisionResponse] = field(
        default_factory=DecisionTool
    )
    normalization: NodeAdapter[NameCleaningRequest, NameCleaningResponse] = field(
        default_factory=NameCleaningAgent
    )
    candidate_selection: NodeAdapter[
        CandidateSelectionRequest, CandidateSelectionResponse
    ] = field(default_factory=CandidateSelectionAgent)
    ontology: NodeAdapter[OntologyRequest, OntologyResponse] = field(
        default_factory=OntologyTool
    )
    bayesian: NodeAdapter[BayesianRequest, BayesianResponse] = field(
        default_factory=BayesianTool
    )
    tag_lookup: NodeAdapter[TagLookupRequest, TagLookupResponse] = field(
        default_factory=TagLookupTool
    )
    ingredient_mapping: NodeAdapter[
        IngredientMappingRequest, IngredientMappingResponse
    ] = field(default_factory=IngredientMappingTool)
    web_aggregate: NodeAdapter[WebAggregateRequest, WebAggregateResponse] = field(
        default_factory=lambda: _default_web_aggregate()
    )
    db_update: NodeAdapter[DbUpdateRequest, DbUpdateResponse] = field(
        default_factory=DbUpdateTool
    )
    prepare_id_factory: Callable[[], UUID] = uuid4
    now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    max_concurrent_web_searches: int = 5


def _default_web_aggregate() -> NodeAdapter[
    WebAggregateRequest, WebAggregateResponse
]:
    from ai_web_search_agent import WebAggregateAgent

    return WebAggregateAgent()


class SupervisorService:
    """Stateless Prepare/Judge entry point for a FastAPI lifespan container."""

    def __init__(self, dependencies: SupervisorServiceDependencies) -> None:
        item_analyzer = ItemGraphAnalyzer(
            ItemGraphDependencies(
                ontology=dependencies.ontology,
                bayesian=dependencies.bayesian,
                tag_lookup=dependencies.tag_lookup,
                decision=dependencies.decision,
                web_collect=dependencies.web_collect,
                ingredient_mapping=dependencies.ingredient_mapping,
                web_aggregate=dependencies.web_aggregate,
                db_update=dependencies.db_update,
                max_concurrent_web_searches=(
                    dependencies.max_concurrent_web_searches
                ),
                now_factory=dependencies.now_factory,
            )
        )
        self.prepare_graph = build_prepare_graph(
            PrepareDependencies(
                ocr=dependencies.ocr,
                normalization=dependencies.normalization,
                prepare_id_factory=dependencies.prepare_id_factory,
            )
        )
        self.judge_graph = build_judge_graph(
            JudgeDependencies(
                candidate_selection=dependencies.candidate_selection,
                item_analyzer=item_analyzer,
                high_risk_review=dependencies.high_risk_review,
                db_update=dependencies.db_update,
            )
        )

    async def prepare(self, request: PrepareRequest) -> PrepareResponse:
        context = RunContext(
            schema_version=request.schema_version,
            trace_id=request.trace_id,
            scan_session_id=request.scan_session_id,
            store_id=None,
        )
        state = await self.prepare_graph.ainvoke(
            {"image": request.image, "locale": request.locale},
            context=context,
        )
        return PrepareResponse.model_validate(state["response"])

    async def judge(self, request: JudgeRequest) -> JudgeResponse:
        context = RunContext(
            schema_version=request.schema_version,
            trace_id=request.trace_id,
            scan_session_id=request.scan_session_id,
            store_id=request.store_id,
        )
        state = await self.judge_graph.ainvoke(
            {"request": request},
            context=context,
        )
        return JudgeResponse.model_validate(state["response"])
