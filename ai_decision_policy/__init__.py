"""⑧ Decision Policy / XAI Agent — rule-based verdict."""

from .flags import FlagCheck, check_flag
from .policy import DecisionOutcome, decide, high_risk_review
from .profile import forbidden_tags

__all__ = [
    "DecisionOutcome",
    "FlagCheck",
    "check_flag",
    "decide",
    "forbidden_tags",
    "high_risk_review",
]
