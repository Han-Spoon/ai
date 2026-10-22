"""⑧ Decision Policy / XAI Agent — rule-based verdict."""

from .flags import FlagCheck, check_flag
from .policy import (
    ConfirmedResult,
    DecisionInput,
    DecisionOutcome,
    ProbabilityResult,
    RiskIngredient,
    TagObservation,
    VariantIngredient,
    decide,
)
from .profile import forbidden_tags

__all__ = [
    "ConfirmedResult",
    "DecisionInput",
    "DecisionOutcome",
    "FlagCheck",
    "ProbabilityResult",
    "RiskIngredient",
    "TagObservation",
    "VariantIngredient",
    "check_flag",
    "decide",
    "forbidden_tags",
]
