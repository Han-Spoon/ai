from datetime import datetime, timezone
from uuid import UUID

import pytest

from ai_supervisor.contracts.common import EvidenceRef, NodeWarning
from ai_supervisor.state import RunContext, merge_evidence, merge_signals


def evidence(source_ref: str = "recipe:1") -> EvidenceRef:
    return EvidenceRef(
        evidence_id="evidence-1",
        evidence_class="soft",
        source_type="recipe",
        source_ref=source_ref,
        verification_status="approved",
        reliability_weight=1.0,
        observed_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
    )


def test_run_context_is_frozen_and_builds_item_context() -> None:
    context = RunContext(
        schema_version="3.0",
        trace_id=UUID("0199a2f0-0000-7000-8000-000000000001"),
        scan_session_id="scan-123",
        store_id=123,
    )
    node_context = context.node_context("item", "scan-123:0")
    assert node_context.store_id == 123
    assert node_context.item_id == "scan-123:0"

    with pytest.raises(AttributeError):
        context.store_id = 456  # type: ignore[misc]


def test_signal_reducer_deduplicates_by_contract_key() -> None:
    warning = NodeWarning(
        code="store_context_missing",
        node="supervisor",
        item_id="scan-123:0",
        message="가게 문맥이 없습니다.",
        forces_caution=True,
    )
    assert merge_signals([warning], [warning]) == [warning]


def test_evidence_reducer_rejects_conflicting_duplicate_id() -> None:
    with pytest.raises(ValueError, match="conflicting evidence_id"):
        merge_evidence([evidence()], [evidence("recipe:2")])
