"""Canonical Supervisor wire contracts from docs/0-agent-supervisor.md."""

from .common import (
    EvidenceRef,
    IngredientRef,
    NodeContext,
    NodeError,
    NodeWarning,
    UserProfile,
)
from .external import JudgeRequest, JudgeResponse, PrepareRequest, PrepareResponse

__all__ = [
    "EvidenceRef",
    "IngredientRef",
    "JudgeRequest",
    "JudgeResponse",
    "NodeContext",
    "NodeError",
    "NodeWarning",
    "PrepareRequest",
    "PrepareResponse",
    "UserProfile",
]
