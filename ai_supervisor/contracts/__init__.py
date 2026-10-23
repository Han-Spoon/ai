"""Canonical Supervisor wire contracts from docs/0-agent-supervisor.md."""

from .common import (
    EvidenceRef,
    IngredientRef,
    NodeContext,
    NodeError,
    NodeWarning,
    UserProfile,
)
from .bayesian import BayesianRequest, BayesianResponse
from .db_update import DbUpdateRequest, DbUpdateResponse
from .decision import (
    DecisionRequest,
    DecisionResponse,
    HighRiskReviewRequest,
    HighRiskReviewResponse,
)
from .exact import ExactFeedbackRequest, ExactFeedbackResponse
from .external import JudgeRequest, JudgeResponse, PrepareRequest, PrepareResponse
from .ocr import OcrRequest, OcrResponse
from .ontology import (
    OntologyRequest,
    OntologyResponse,
    TagLookupRequest,
    TagLookupResponse,
)
from .web_search import (
    WebAggregateRequest,
    WebAggregateResponse,
    WebCollectRequest,
    WebCollectResponse,
)

__all__ = [
    "BayesianRequest",
    "BayesianResponse",
    "DbUpdateRequest",
    "DbUpdateResponse",
    "DecisionRequest",
    "DecisionResponse",
    "EvidenceRef",
    "ExactFeedbackRequest",
    "ExactFeedbackResponse",
    "HighRiskReviewRequest",
    "HighRiskReviewResponse",
    "IngredientRef",
    "JudgeRequest",
    "JudgeResponse",
    "NodeContext",
    "NodeError",
    "NodeWarning",
    "OcrRequest",
    "OcrResponse",
    "OntologyRequest",
    "OntologyResponse",
    "TagLookupRequest",
    "TagLookupResponse",
    "PrepareRequest",
    "PrepareResponse",
    "UserProfile",
    "WebAggregateRequest",
    "WebAggregateResponse",
    "WebCollectRequest",
    "WebCollectResponse",
]
