"""LangGraph orchestration for the Prepare phase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from ai_normalization.contracts import (
    NameCleaningRequest,
    NameCleaningResponse,
    OcrMenuItem,
)

from .adapters import NodeAdapter
from .contracts.common import NodeError, NodeWarning
from .contracts.external import (
    PrepareResponse,
    PrepareSignals,
    PreparedItem,
    ScanQuality,
)
from .contracts.ocr import OcrImageRef, OcrRequest, OcrResponse
from .hashing import prepared_items_hash
from .routers import route_after_quality
from .state import PrepareState, RunContext


class PrepareGraphInvariantError(ValueError):
    """A Prepare adapter changed immutable context or pass-through fields."""


@dataclass(frozen=True)
class PrepareDependencies:
    ocr: NodeAdapter[OcrRequest, OcrResponse]
    normalization: NodeAdapter[NameCleaningRequest, NameCleaningResponse]
    prepare_id_factory: Callable[[], UUID] = uuid4


def build_prepare_graph(dependencies: PrepareDependencies) -> Any:
    """Build and compile the stateless ① → quality → ②-A Prepare graph."""

    async def ocr_node(
        state: PrepareState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        context = runtime.context.node_context("scan", None)
        image = OcrImageRef(
            **state["image"].model_dump(),
            image_url=None,
        )
        request = OcrRequest(
            context=context,
            image=image,
            warnings=list(state.get("warnings", [])),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.ocr.run(request)
        if response.context != context:
            raise PrepareGraphInvariantError("OCR changed immutable Prepare context")

        scan_quality = ScanQuality.model_validate(
            response.scan_quality.model_dump(exclude={"score"})
        )
        warnings = list(response.warnings)
        if scan_quality.status == "low_confidence":
            warnings.append(_scan_quality_warning())

        return {
            "ocr_response": response,
            "scan_quality": scan_quality,
            "warnings": _dedupe_signals(warnings),
            "errors": list(response.errors),
        }

    def quality_route(state: PrepareState) -> str:
        return route_after_quality(state["scan_quality"])

    async def clean_names_node(
        state: PrepareState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        context = runtime.context.node_context("scan", None)
        ocr_response: OcrResponse = state["ocr_response"]
        request = NameCleaningRequest(
            context=context,
            items=[
                OcrMenuItem.model_validate(item.model_dump())
                for item in ocr_response.items
            ],
            warnings=list(state.get("warnings", [])),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.normalization.run(request)
        _validate_cleaning_response(request, response)
        return {
            "normalization_response": response,
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }

    def finalize_retake_node(
        state: PrepareState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        return _finalize_prepare(state, runtime.context, dependencies, [])

    def finalize_prepared_node(
        state: PrepareState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        response: NameCleaningResponse = state["normalization_response"]
        items = [
            PreparedItem.model_validate(
                item.model_dump(exclude={"cleaning_status"})
            )
            for item in response.items
            if item.cleaning_status != "invalid"
        ]
        return _finalize_prepare(state, runtime.context, dependencies, items)

    graph = StateGraph(PrepareState, context_schema=RunContext)
    graph.add_node("ocr", ocr_node)
    graph.add_node("clean_names", clean_names_node)
    graph.add_node("finalize_retake", finalize_retake_node)
    graph.add_node("finalize_prepared", finalize_prepared_node)
    graph.add_edge(START, "ocr")
    graph.add_conditional_edges(
        "ocr",
        quality_route,
        {"normalize": "clean_names", "finalize": "finalize_retake"},
    )
    graph.add_edge("clean_names", "finalize_prepared")
    graph.add_edge("finalize_retake", END)
    graph.add_edge("finalize_prepared", END)
    return graph.compile(checkpointer=False, name="prepare_graph")


def _validate_cleaning_response(
    request: NameCleaningRequest, response: NameCleaningResponse
) -> None:
    if response.context != request.context:
        raise PrepareGraphInvariantError(
            "Normalization changed immutable Prepare context"
        )
    request_keys = [(item.item_id, item.source_index) for item in request.items]
    response_keys = [(item.item_id, item.source_index) for item in response.items]
    if request_keys != response_keys:
        raise PrepareGraphInvariantError(
            "Normalization must preserve every OCR item and source order"
        )

    pass_through_fields = set(OcrMenuItem.model_fields)
    for source, cleaned in zip(request.items, response.items):
        if source.model_dump() != cleaned.model_dump(include=pass_through_fields):
            raise PrepareGraphInvariantError(
                f"Normalization changed OCR pass-through fields for {source.item_id}"
            )


def _finalize_prepare(
    state: PrepareState,
    context: RunContext,
    dependencies: PrepareDependencies,
    items: list[PreparedItem],
) -> dict[str, Any]:
    quality = state["scan_quality"]
    warnings = _dedupe_signals(list(state.get("warnings", [])))
    errors = list(state.get("errors", []))
    new_errors: list[NodeError] = []
    if quality.status != "needs_retake" and not items:
        no_items = NodeError(
            code="no_menu_items",
            node="supervisor",
            item_id=None,
            message="정제 후 판정 가능한 메뉴가 없습니다.",
            retryable=False,
            fallback="retake",
        )
        if not any(error.code == no_items.code for error in errors):
            errors.append(no_items)
            new_errors.append(no_items)

    signals = PrepareSignals(
        scan_quality_status=quality.status,
        warnings=[warning for warning in warnings if warning.forces_caution],
    )
    response = PrepareResponse(
        schema_version=context.schema_version,
        trace_id=context.trace_id,
        prepare_id=dependencies.prepare_id_factory(),
        scan_session_id=context.scan_session_id,
        prepared_items_hash=prepared_items_hash(items, signals),
        scan_quality=quality,
        prepare_signals=signals,
        prepared_items=items,
        warnings=warnings,
        errors=errors,
    )
    return {
        "prepared_items": items,
        "prepare_signals": signals,
        "response": response,
        "errors": new_errors,
    }


def _scan_quality_warning() -> NodeWarning:
    return NodeWarning(
        code="scan_quality_low_confidence",
        node="ocr",
        item_id=None,
        message="사진 품질이 낮아 이후 판정에서 SAFE를 제한해야 합니다.",
        forces_caution=True,
    )


def _dedupe_signals(signals: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[tuple[str, str, str | None]] = set()
    for signal in signals:
        key = (signal.code, signal.node, signal.item_id)
        if key not in seen:
            result.append(signal)
            seen.add(key)
    return result
