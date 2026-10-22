"""Pure routing helpers shared by the future LangGraph builders."""

from __future__ import annotations

from typing import Literal

from .contracts.external import ScanQuality


def route_after_quality(quality: ScanQuality) -> Literal["normalize", "finalize"]:
    return "finalize" if quality.status == "needs_retake" else "normalize"


def owner_interaction_allowed(store_id: int | None) -> bool:
    return store_id is not None
