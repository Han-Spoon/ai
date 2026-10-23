"""LangGraph orchestration for one menu's implemented DB analysis path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from ai_exact_feedback import ExactFeedbackTool

from .adapters import NodeAdapter
from .contracts.bayesian import (
    BayesianIngredient,
    BayesianPriors,
    BayesianRequest,
    BayesianResponse,
)
from .contracts.common import NodeError, NodeWarning
from .contracts.decision import (
    ConfirmedResult,
    DecisionMenu,
    DecisionProbabilityResult,
    DecisionRequest,
    DecisionResponse,
    DecisionTagObservation,
    LocalizedName,
)
from .contracts.exact import ExactFeedbackRequest, ExactFeedbackResponse, ExactMenu
from .contracts.external import ItemResult
from .contracts.ontology import (
    ConfirmedIngredient,
    OntologyMenuInput,
    OntologyRequest,
    OntologyResponse,
    TagLookupRequest,
    TagLookupResponse,
)
from .judge_graph import ItemAnalysisError
from .routers import route_after_exact, route_after_ontology, route_item_start
from .state import ItemAnalysisOutput, ItemState, ItemTask, RunContext


class ItemGraphInvariantError(ValueError):
    """An Item adapter changed immutable context or pass-through evidence."""


@dataclass(frozen=True)
class ItemGraphDependencies:
    ontology: NodeAdapter[OntologyRequest, OntologyResponse]
    bayesian: NodeAdapter[BayesianRequest, BayesianResponse]
    tag_lookup: NodeAdapter[TagLookupRequest, TagLookupResponse]
    decision: NodeAdapter[DecisionRequest, DecisionResponse]
    now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


class ItemGraphAnalyzer:
    """Judge-compatible analyzer backed by one compiled Item Graph."""

    def __init__(self, dependencies: ItemGraphDependencies) -> None:
        self.graph = build_item_graph(dependencies)

    async def run(
        self, task: ItemTask, context: RunContext
    ) -> ItemAnalysisOutput:
        result = await self.graph.ainvoke({"task": task}, context=context)
        return result["analysis_output"]


def build_item_graph(dependencies: ItemGraphDependencies) -> Any:
    """Compile ③ → ④ → ⑤ → tag lookup → ⑧ for one menu."""

    def prepare_item_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        _validate_task_context(task, runtime.context)
        warnings = list(task.warnings)
        if runtime.context.store_id is None and not any(
            warning.code == "store_context_missing" for warning in warnings
        ):
            warnings.append(_store_context_warning(task))
        knowledge = (
            task.knowledge.selected.ontology
            if task.knowledge.selected is not None
            else task.knowledge.fallback_ontology
        )
        if knowledge.status == "unavailable":
            warnings.append(_slice_warning(task, "ontology"))
        if task.ingredient_graph.status == "unavailable":
            warnings.append(_slice_warning(task, "ingredient_graph"))
        return {
            "evidence": [
                observation.evidence
                for observation in task.prepared_item.risk_tag_observations
            ],
            "commands": [],
            "warnings": _dedupe_signals(warnings),
            "errors": list(task.errors),
        }

    def start_route(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> str:
        return route_item_start(state["task"], runtime.context.store_id)

    async def exact_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        selected = task.knowledge.selected
        if selected is None:
            raise ItemGraphInvariantError("exact path requires selected knowledge")
        candidate = task.selection.selected_candidate
        if candidate is None:
            raise ItemGraphInvariantError("exact path requires selected candidate")

        context = runtime.context.node_context(
            "item", task.prepared_item.item_id, include_store=True
        )
        warnings = list(state.get("warnings", []))
        if selected.exact_feedback.status == "unavailable":
            warnings.append(_slice_warning(task, "exact_feedback"))
        request = ExactFeedbackRequest(
            context=context,
            menu=ExactMenu(
                menu_id=candidate.menu_id,
                menu_category_id=candidate.menu_category_id,
                normalized_menu_name=task.prepared_item.normalized_menu_name,
            ),
            scope_hint="exact",
            warnings=_dedupe_signals(warnings),
            errors=list(state.get("errors", [])),
        )
        response = await ExactFeedbackTool(
            task.knowledge, clock=dependencies.now_factory
        ).run(request)
        _validate_response_signals(request, response, "Exact feedback")
        if response.menu_id != request.menu.menu_id:
            raise ItemGraphInvariantError(
                "Exact feedback changed selected menu_id"
            )
        evidence = [
            confirmation.evidence for confirmation in response.confirmations
        ] + [confirmation.evidence for confirmation in response.flag_confirmations]
        update: dict[str, Any] = {
            "exact_response": response,
            "evidence": evidence,
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }
        if route_after_exact(response) == "tag_lookup":
            update["route"] = "exact_only"
        return update

    def exact_route(state: ItemState) -> str:
        return route_after_exact(state["exact_response"])

    async def ontology_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        exact = state.get("exact_response")
        selected = task.knowledge.selected
        knowledge = (
            selected.ontology
            if selected is not None
            else task.knowledge.fallback_ontology
        )
        context = runtime.context.node_context(
            "item", task.prepared_item.item_id, include_store=True
        )
        eligible = [
            confirmation
            for confirmation in (exact.confirmations if exact else [])
            if confirmation.override_eligible
            and confirmation.status in {"present", "absent"}
        ]
        request = OntologyRequest(
            context=context,
            menu=OntologyMenuInput(
                menu_id=(
                    task.selection.selected_candidate.menu_id
                    if task.selection.selected_candidate is not None
                    else None
                ),
                normalized_menu_name=task.prepared_item.normalized_menu_name,
                match_candidates=task.selection.match_candidates,
                residual_tokens=task.prepared_item.residual_tokens,
            ),
            unconfirmed_only=True,
            confirmed_ingredients=[
                ConfirmedIngredient(
                    ingredient=confirmation.ingredient,
                    status=confirmation.status,
                )
                for confirmation in eligible
            ],
            anomaly_locked_ingredients=[
                confirmation.ingredient
                for confirmation in (exact.confirmations if exact else [])
                if confirmation.flagged_anomaly
            ],
            knowledge=knowledge,
            ingredient_graph=task.ingredient_graph,
            warnings=list(state.get("warnings", [])),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.ontology.run(request)
        _validate_response_signals(request, response, "Ontology")
        if (
            selected is not None
            and response.menu.exists_in_db
            and response.menu.menu_id != selected.menu_id
        ):
            raise ItemGraphInvariantError(
                "Ontology changed selected menu_id"
            )
        if response.cache_command is not None and (
            response.cache_command.item_id != task.prepared_item.item_id
            or response.cache_command.card_id is not None
        ):
            raise ItemGraphInvariantError(
                "ontology cache command changed item scope"
            )
        route = route_after_ontology(response)
        update: dict[str, Any] = {
            "ontology_response": response,
            "evidence": list(response.evidence),
            "commands": (
                [response.cache_command]
                if response.cache_command is not None
                else []
            ),
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }
        if route == "bayesian":
            update["route"] = (
                "db_variant_bayes" if response.menu.is_variant else "db_bayes"
            )
        return update

    def ontology_route(state: ItemState) -> str:
        return route_after_ontology(state["ontology_response"])

    async def bayesian_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        selected = task.knowledge.selected
        if selected is None:
            raise ItemGraphInvariantError(
                "DB Bayesian path requires selected candidate priors"
            )
        ontology: OntologyResponse = state["ontology_response"]
        context = runtime.context.node_context(
            "item", task.prepared_item.item_id, include_store=True
        )
        warnings = list(state.get("warnings", []))
        if selected.priors.status == "unavailable":
            warnings.append(_slice_warning(task, "priors"))
        if task.source_reliability.status == "unavailable":
            warnings.append(_slice_warning(task, "source_reliability"))
        request = BayesianRequest(
            context=context,
            menu_category_id=ontology.menu.menu_category_id,
            ingredients=[
                BayesianIngredient(
                    ingredient=ingredient.ingredient,
                    constraint_tags=ingredient.constraint_tags,
                    role=ingredient.role,
                    source=ingredient.source,
                    depth=ingredient.depth,
                    curated=ingredient.curated,
                    observation_basis=ingredient.observation_basis,
                    observations=ingredient.observations,
                    anomaly_locked=ingredient.anomaly_locked,
                    evidence_refs=ingredient.evidence_refs,
                )
                for ingredient in ontology.ingredients
            ],
            priors=BayesianPriors.model_validate(
                {
                    "status": selected.priors.status,
                    "store": selected.priors.store,
                    "cluster": [],
                    "global": [],
                    "evidence": selected.priors.evidence,
                }
            ),
            source_reliability=task.source_reliability,
            warnings=_dedupe_signals(warnings),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.bayesian.run(request)
        _validate_bayesian_response(request, response)
        return {
            "bayesian_response": response,
            "evidence": list(selected.priors.evidence),
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }

    async def tag_lookup_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        exact = state.get("exact_response")
        confirmations = list(exact.confirmations) if exact else []
        ingredient_ids: list[int] = []
        for confirmation in confirmations:
            if confirmation.ingredient.ingredient_id is None:
                raise ItemGraphInvariantError(
                    "exact confirmation requires ingredient_id for tag lookup"
                )
            ingredient_ids.append(confirmation.ingredient.ingredient_id)
        context = runtime.context.node_context(
            "item", task.prepared_item.item_id, include_store=True
        )
        request = TagLookupRequest(
            context=context,
            ingredient_ids=ingredient_ids,
            ingredient_graph=task.ingredient_graph,
            warnings=list(state.get("warnings", [])),
            errors=list(state.get("errors", [])),
        )
        if not ingredient_ids:
            response = TagLookupResponse(
                context=request.context,
                results=[],
                warnings=request.warnings,
                errors=request.errors,
            )
        else:
            response = await dependencies.tag_lookup.run(request)
        _validate_tag_lookup_response(request, response)
        return {
            "tag_lookup_response": response,
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }

    async def decision_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        exact = state.get("exact_response")
        ontology = state.get("ontology_response")
        bayesian = state.get("bayesian_response")
        tag_lookup: TagLookupResponse = state["tag_lookup_response"]
        tags_by_id = {
            result.ingredient_id: result for result in tag_lookup.results
        }
        confirmed_results: list[ConfirmedResult] = []
        for confirmation in exact.confirmations if exact else []:
            ingredient_id = confirmation.ingredient.ingredient_id
            tagged = tags_by_id.get(ingredient_id) if ingredient_id else None
            if tagged is None:
                raise ItemGraphInvariantError(
                    "tag lookup omitted an exact confirmation"
                )
            confirmed_results.append(
                ConfirmedResult(
                    ingredient=confirmation.ingredient,
                    constraint_tags=tagged.constraint_tags,
                    role=tagged.role,
                    status=confirmation.status,
                    override_eligible=confirmation.override_eligible,
                    expired=confirmation.expired,
                    evidence_refs=[confirmation.evidence.evidence_id],
                )
            )
        probabilities = [
            DecisionProbabilityResult(
                ingredient=result.ingredient,
                constraint_tags=result.constraint_tags,
                role=result.role,
                posterior_mean=result.posterior_mean,
                confidence=result.confidence,
                prior_source=result.prior_source,
                anomaly_locked=result.anomaly_locked,
                evidence_refs=result.evidence_refs,
            )
            for result in (bayesian.probabilities if bayesian else [])
        ]
        candidate = task.selection.selected_candidate
        menu = DecisionMenu(
            menu_id=candidate.menu_id if candidate else None,
            menu_category_id=(candidate.menu_category_id if candidate else None),
            normalized_menu_name=task.prepared_item.normalized_menu_name,
        )
        context = runtime.context.node_context(
            "item", task.prepared_item.item_id, include_store=True
        )
        request = DecisionRequest(
            context=context,
            menu=menu,
            confirmed_results=confirmed_results,
            probability_results=probabilities,
            proposed_variant_ingredients=[],
            risk_tag_observations=[
                DecisionTagObservation(
                    tag=observation.tag,
                    confidence=observation.confidence,
                    evidence_refs=[observation.evidence.evidence_id],
                )
                for observation in task.prepared_item.risk_tag_observations
            ],
            menu_ambiguity_flags=_menu_ambiguity_flags(task, ontology),
            flag_confirmations=(
                list(exact.flag_confirmations) if exact else []
            ),
            owner_question_states=(
                list(exact.owner_question_states) if exact else []
            ),
            no_information=False,
            information_status=_information_status(
                state, confirmed_results, probabilities
            ),
            owner_interaction_allowed=runtime.context.store_id is not None,
            user_profile=task.user_profile,
            locale=task.locale,
            localized_names=_localized_names(
                task, confirmed_results, probabilities
            ),
            warnings=list(state.get("warnings", [])),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.decision.run(request)
        _validate_response_signals(request, response, "Decision")
        if response.menu != request.menu:
            raise ItemGraphInvariantError("Decision changed menu identity")
        if response.information_status != request.information_status:
            raise ItemGraphInvariantError("Decision changed information_status")
        if response.risk_level == "safe" and (
            request.information_status != "complete"
            or any(warning.forces_caution for warning in request.warnings)
            or bool(request.probability_results)
            or bool(request.proposed_variant_ingredients)
            or not request.confirmed_results
            or any(
                result.anomaly_locked
                for result in request.probability_results
            )
        ):
            raise ItemGraphInvariantError(
                "Decision returned safe while a safety restriction is active"
            )
        return {
            "decision_response": response,
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }

    def finish_item_node(state: ItemState) -> dict[str, Any]:
        task = state["task"]
        decision: DecisionResponse = state["decision_response"]
        evidence = list(state.get("evidence", []))
        evidence_ids = {item.evidence_id for item in evidence}
        refs = decision.evidence_refs + [
            ref
            for ingredient in decision.risk_ingredients
            for ref in ingredient.evidence_refs
        ]
        if any(ref not in evidence_ids for ref in refs):
            raise ItemGraphInvariantError(
                "Decision evidence_refs must resolve inside the item registry"
            )
        candidate = task.selection.selected_candidate
        result = ItemResult(
            item_id=task.prepared_item.item_id,
            source_index=task.prepared_item.source_index,
            menu_id=candidate.menu_id if candidate else None,
            menu_category_id=candidate.menu_category_id if candidate else None,
            normalization_status=task.selection.normalization_status,
            match_type=candidate.match_type if candidate else None,
            selection_method=task.selection.selection_method,
            status="completed",
            route=state["route"],
            risk_level=decision.risk_level,
            evidence_basis=decision.evidence_basis,
            information_status=decision.information_status,
            risk_ingredients=[
                ingredient.model_dump(exclude={"anomaly_locked"})
                for ingredient in decision.risk_ingredients
            ],
            matched_tags=decision.matched_tags,
            evidence_refs=decision.evidence_refs,
            message=decision.message,
            owner_cards=decision.owner_cards,
            warnings=decision.warnings,
            errors=decision.errors,
        )
        output = ItemAnalysisOutput(
            result=result,
            evidence=tuple(evidence),
            commands=tuple(state.get("commands", [])),
        )
        return {"result": result, "analysis_output": output}

    def pending_variant_node(state: ItemState) -> dict[str, Any]:
        raise ItemAnalysisError(
            "variant_review_not_implemented",
            "런타임 변형 검토 경로는 7번 구현에서 연결됩니다.",
            retryable=False,
        )

    def pending_web_node(state: ItemState) -> dict[str, Any]:
        raise ItemAnalysisError(
            "web_path_not_implemented",
            "DB에 없는 메뉴의 웹 검색 경로는 6번 구현에서 연결됩니다.",
            retryable=False,
        )

    graph = StateGraph(ItemState, context_schema=RunContext)
    graph.add_node("prepare_item", prepare_item_node)
    graph.add_node("exact", exact_node)
    graph.add_node("ontology", ontology_node)
    graph.add_node("bayesian", bayesian_node)
    graph.add_node("tag_lookup", tag_lookup_node)
    graph.add_node("decision", decision_node)
    graph.add_node("finish_item", finish_item_node)
    graph.add_node("variant_pending", pending_variant_node)
    graph.add_node("web_pending", pending_web_node)
    graph.add_edge(START, "prepare_item")
    graph.add_conditional_edges(
        "prepare_item", start_route, {"exact": "exact", "ontology": "ontology"}
    )
    graph.add_conditional_edges(
        "exact",
        exact_route,
        {"tag_lookup": "tag_lookup", "ontology": "ontology"},
    )
    graph.add_conditional_edges(
        "ontology",
        ontology_route,
        {
            "bayesian": "bayesian",
            "variant_pending": "variant_pending",
            "web_pending": "web_pending",
        },
    )
    graph.add_edge("bayesian", "tag_lookup")
    graph.add_edge("tag_lookup", "decision")
    graph.add_edge("decision", "finish_item")
    graph.add_edge("finish_item", END)
    return graph.compile(checkpointer=False, name="item_graph")


def _validate_task_context(task: ItemTask, context: RunContext) -> None:
    expected = f"{context.scan_session_id}:{task.prepared_item.source_index}"
    if task.prepared_item.item_id != expected:
        raise ItemGraphInvariantError("ItemTask does not belong to RunContext")
    if task.knowledge.item_id != task.prepared_item.item_id:
        raise ItemGraphInvariantError("ItemTask knowledge has a different item_id")


def _validate_response_signals(request: Any, response: Any, node: str) -> None:
    if response.context != request.context:
        raise ItemGraphInvariantError(f"{node} changed immutable item context")
    for field in ("warnings", "errors"):
        required = {
            (signal.code, signal.node, signal.item_id)
            for signal in getattr(request, field)
        }
        actual = {
            (signal.code, signal.node, signal.item_id)
            for signal in getattr(response, field)
        }
        if not required <= actual:
            raise ItemGraphInvariantError(f"{node} dropped input {field}")


def _validate_bayesian_response(
    request: BayesianRequest, response: BayesianResponse
) -> None:
    _validate_response_signals(request, response, "Bayesian")
    if len(response.probabilities) != len(request.ingredients):
        raise ItemGraphInvariantError(
            "Bayesian must return one probability per ingredient"
        )
    fields = (
        "ingredient",
        "constraint_tags",
        "role",
        "source",
        "depth",
        "anomaly_locked",
        "evidence_refs",
    )
    for source, result in zip(request.ingredients, response.probabilities):
        if any(getattr(source, field) != getattr(result, field) for field in fields):
            raise ItemGraphInvariantError(
                "Bayesian changed ingredient pass-through fields"
            )


def _validate_tag_lookup_response(
    request: TagLookupRequest, response: TagLookupResponse
) -> None:
    _validate_response_signals(request, response, "Tag lookup")
    if [result.ingredient_id for result in response.results] != request.ingredient_ids:
        raise ItemGraphInvariantError(
            "Tag lookup must return every requested ingredient in order"
        )


def _menu_ambiguity_flags(
    task: ItemTask, ontology: OntologyResponse | None
) -> list[str]:
    if ontology is not None:
        return list(ontology.menu_ambiguity_flags)
    selected = task.knowledge.selected
    if selected is not None and selected.ontology.menu is not None:
        return list(selected.ontology.menu.ambiguity_flags)
    return []


def _localized_names(
    task: ItemTask,
    confirmed: list[ConfirmedResult],
    probabilities: list[DecisionProbabilityResult],
) -> list[LocalizedName]:
    used_ids = {
        item.ingredient.ingredient_id
        for item in [*confirmed, *probabilities]
        if item.ingredient.ingredient_id is not None
    }
    return [
        LocalizedName(
            ingredient_id=ingredient.ingredient_id,
            localized_name=ingredient.localized_name,
        )
        for ingredient in task.ingredient_graph.ingredients
        if ingredient.ingredient_id in used_ids
        and ingredient.localized_name is not None
    ]


def _information_status(
    state: ItemState,
    confirmed: list[ConfirmedResult],
    probabilities: list[DecisionProbabilityResult],
) -> str:
    if state.get("errors"):
        return "partial"
    if any(
        warning.forces_caution and warning.code != "store_context_missing"
        for warning in state.get("warnings", [])
    ):
        return "partial"
    if not confirmed and not probabilities:
        return "partial"
    return "complete"


def _slice_warning(task: ItemTask, slice_name: str) -> NodeWarning:
    return NodeWarning(
        code="knowledge_slice_unavailable",
        node="supervisor",
        item_id=task.prepared_item.item_id,
        message=f"{slice_name} 지식 데이터를 불러오지 못했습니다.",
        forces_caution=True,
    )


def _store_context_warning(task: ItemTask) -> NodeWarning:
    return NodeWarning(
        code="store_context_missing",
        node="supervisor",
        item_id=task.prepared_item.item_id,
        message="가게 정보가 없어 확정 정보와 저장 기능을 사용하지 않습니다.",
        forces_caution=True,
    )


def _dedupe_signals(
    signals: list[NodeWarning] | list[NodeError],
) -> list[NodeWarning] | list[NodeError]:
    result: list[NodeWarning] | list[NodeError] = []
    seen: set[tuple[str, str, str | None]] = set()
    for signal in signals:
        key = (signal.code, signal.node, signal.item_id)
        if key not in seen:
            result.append(signal)
            seen.add(key)
    return result
