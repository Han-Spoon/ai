"""LangGraph orchestration for one menu's DB, variant, and web paths."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

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
from .contracts.db_update import (
    CreateReviewItemPayload,
    DbOperation,
    DbUpdateRequest,
    DbUpdateResponse,
    SaveWebSearchCachePayload,
)
from .contracts.decision import (
    ConfirmedResult,
    DecisionMenu,
    DecisionProbabilityResult,
    DecisionRequest,
    DecisionResponse,
    DecisionTagObservation,
    FoundIngredient,
    LocalizedName,
    ProposedVariantIngredient,
)
from .contracts.exact import ExactFeedbackRequest, ExactFeedbackResponse, ExactMenu
from .contracts.external import (
    ItemResult,
    PersistenceCommand,
    ResultOwnerCard,
    WebSource,
)
from .contracts.ontology import (
    ConfirmedIngredient,
    IngredientMappingRequest,
    IngredientMappingResponse,
    OntologyMenuInput,
    OntologyRequest,
    OntologyResponse,
    TagLookupRequest,
    TagLookupResponse,
)
from .contracts.web_search import (
    MappedIngredient,
    MappedSource,
    WebAggregateRequest,
    WebAggregateResponse,
    WebCollectMenu,
    WebCollectRequest,
    WebCollectResponse,
    WebMenu,
)
from .routers import (
    route_after_exact,
    route_after_ontology,
    route_after_web_aggregate,
    route_after_web_collect,
    route_item_start,
)
from .state import (
    ItemAnalysisOutput,
    ItemReviewCandidate,
    ItemState,
    ItemTask,
    PendingDbOperation,
    RunContext,
)


class ItemGraphInvariantError(ValueError):
    """An Item adapter changed immutable context or pass-through evidence."""


@dataclass(frozen=True)
class ItemGraphDependencies:
    ontology: NodeAdapter[OntologyRequest, OntologyResponse]
    bayesian: NodeAdapter[BayesianRequest, BayesianResponse]
    tag_lookup: NodeAdapter[TagLookupRequest, TagLookupResponse]
    decision: NodeAdapter[DecisionRequest, DecisionResponse]
    web_collect: NodeAdapter[WebCollectRequest, WebCollectResponse]
    ingredient_mapping: NodeAdapter[
        IngredientMappingRequest, IngredientMappingResponse
    ]
    web_aggregate: NodeAdapter[WebAggregateRequest, WebAggregateResponse]
    # Compatibility-only constructor field. Judge Graph owns the single ⑦ call.
    db_update: NodeAdapter[DbUpdateRequest, DbUpdateResponse] | None = None
    max_concurrent_web_searches: int = 5
    now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrent_web_searches <= 5:
            raise ValueError("max_concurrent_web_searches must be between 1 and 5")


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
    """Compile the documented DB, runtime-variant, and web paths."""

    web_search_slots: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}

    def current_web_search_slots() -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if loop not in web_search_slots:
            web_search_slots[loop] = asyncio.Semaphore(
                dependencies.max_concurrent_web_searches
            )
        return web_search_slots[loop]

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
            # Owner answers are never locked any more, even when flagged as
            # anomalous (10/27 meeting, ③ §1-6). The field stays until #340 decides
            # whether the anomaly flag itself goes away.
            anomaly_locked_ingredients=[],
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
        suggestion = response.variant_suggestion
        update: dict[str, Any] = {
            "ontology_response": response,
            "evidence": [
                *response.evidence,
                *([suggestion.evidence] if suggestion is not None else []),
            ],
            "commands": (
                [response.cache_command]
                if response.cache_command is not None
                else []
            ),
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }
        if route in {"bayesian", "variant_review"}:
            update["route"] = (
                "db_variant_bayes" if response.menu.is_variant else "db_bayes"
            )
        return update

    def ontology_route(state: ItemState) -> str:
        return route_after_ontology(state["ontology_response"])

    def variant_review_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        ontology: OntologyResponse = state["ontology_response"]
        suggestion = ontology.variant_suggestion
        if (
            ontology.menu.variant_origin != "runtime_tagged"
            or suggestion is None
        ):
            raise ItemGraphInvariantError(
                "variant review requires one runtime variant suggestion"
            )
        if (
            suggestion.base_menu_id != ontology.menu.base_menu_id
            or suggestion.remain_token != ontology.menu.remain_token
        ):
            raise ItemGraphInvariantError(
                "variant suggestion changed base menu identity"
            )
        if runtime.context.store_id is None:
            return {}

        return {
            "operations": [
                PendingDbOperation(
                    item_id=task.prepared_item.item_id,
                    operation=DbOperation(
                        operation_id=(
                            f"operation:{task.prepared_item.item_id}:variant-review"
                        ),
                        operation_type="create_review_item",
                        payload=CreateReviewItemPayload(
                            review_origin="variant_suggestion",
                            menu=WebMenu(
                                menu_id=None,
                                normalized_menu_name=(
                                    task.prepared_item.normalized_menu_name
                                ),
                            ),
                            web_sources=[],
                            review_notes=[],
                            variant_suggestion=suggestion,
                        ),
                    ),
                )
            ],
        }

    async def web_collect_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        cache = task.knowledge.web_search_cache
        warnings = list(state.get("warnings", []))
        if cache.status == "unavailable":
            warnings.append(_slice_warning(task, "web_search_cache"))
        context = runtime.context.node_context(
            "item", task.prepared_item.item_id, include_store=True
        )
        request = WebCollectRequest(
            context=context,
            menu=WebCollectMenu(
                menu_id=None,
                normalized_menu_name=task.prepared_item.normalized_menu_name,
                locale_hint=task.prepared_item.locale_hint,
            ),
            max_sources=10,
            cached_sources=list(cache.cached_sources),
            next_rank=cache.next_rank,
            warnings=_dedupe_signals(warnings),
            errors=list(state.get("errors", [])),
        )
        async with current_web_search_slots():
            response = await dependencies.web_collect.run(request)
        _validate_web_collect_response(request, response)
        combined_sources = [*request.cached_sources, *response.sources]
        _validate_web_sources(combined_sources)
        update: dict[str, Any] = {
            "web_collect_response": response,
            "evidence": [source.evidence for source in combined_sources],
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }
        if not combined_sources:
            update["route"] = "no_information"
        return update

    def web_collect_route(state: ItemState) -> str:
        task = state["task"]
        return route_after_web_collect(
            state["web_collect_response"],
            has_cached_sources=bool(task.knowledge.web_search_cache.cached_sources),
        )

    async def ingredient_mapping_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        collect: WebCollectResponse = state["web_collect_response"]
        sources = [
            *task.knowledge.web_search_cache.cached_sources,
            *collect.sources,
        ]
        names = _unique_ingredient_names(sources)
        context = runtime.context.node_context(
            "item", task.prepared_item.item_id, include_store=True
        )
        request = IngredientMappingRequest(
            context=context,
            ingredient_names=names,
            ingredient_graph=task.ingredient_graph,
            warnings=list(state.get("warnings", [])),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.ingredient_mapping.run(request)
        _validate_ingredient_mapping_response(request, response)
        return {
            "ingredient_mapping_response": response,
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }

    async def web_aggregate_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        collect: WebCollectResponse = state["web_collect_response"]
        mapping: IngredientMappingResponse = state["ingredient_mapping_response"]
        mapped_by_name = {result.input_name: result for result in mapping.results}
        sources: list[MappedSource] = []
        for origin, raw_sources in (
            ("cache", task.knowledge.web_search_cache.cached_sources),
            ("new", collect.sources),
        ):
            for source in raw_sources:
                sources.append(
                    MappedSource(
                        source_url=source.source_url,
                        title=source.title,
                        source_origin=origin,
                        corpus=_web_corpus(source.source_url),
                        evidence_id=source.evidence.evidence_id,
                        ingredients=[
                            MappedIngredient(
                                ingredient=mapped_by_name[
                                    ingredient.canonical_name
                                ].ingredient,
                                constraint_tags=mapped_by_name[
                                    ingredient.canonical_name
                                ].constraint_tags,
                                role=mapped_by_name[
                                    ingredient.canonical_name
                                ].role,
                            )
                            for ingredient in source.ingredients
                        ],
                    )
                )
        context = runtime.context.node_context(
            "item", task.prepared_item.item_id, include_store=True
        )
        request = WebAggregateRequest(
            context=context,
            menu=WebMenu(
                menu_id=None,
                normalized_menu_name=task.prepared_item.normalized_menu_name,
            ),
            mapped_sources=sources,
            warnings=list(state.get("warnings", [])),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.web_aggregate.run(request)
        _validate_web_aggregate_response(request, response)
        evidence_ids = {source.evidence_id for source in sources}
        if any(
            ref not in evidence_ids
            for ingredient in response.ingredients
            for ref in ingredient.evidence_refs
        ):
            raise ItemGraphInvariantError(
                "Web aggregate evidence_refs must resolve to web sources"
            )
        update: dict[str, Any] = {
            "web_aggregate_response": response,
            "warnings": list(response.warnings),
            "errors": list(response.errors),
        }
        if response.found:
            update["route"] = "web_bayes"
        else:
            update["route"] = "no_information"
        return update

    def web_aggregate_route(state: ItemState) -> str:
        return route_after_web_aggregate(state["web_aggregate_response"])

    def web_review_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        collect: WebCollectResponse = state["web_collect_response"]
        aggregate: WebAggregateResponse = state["web_aggregate_response"]
        if runtime.context.store_id is None or not collect.sources:
            return {}
        menu = WebMenu(
            menu_id=None,
            normalized_menu_name=task.prepared_item.normalized_menu_name,
        )
        operations = [
            DbOperation(
                operation_id=f"operation:{task.prepared_item.item_id}:web-review",
                operation_type="create_review_item",
                payload=CreateReviewItemPayload(
                    review_origin="web_search",
                    menu=menu,
                    web_sources=list(collect.sources),
                    review_notes=list(aggregate.review_notes),
                    variant_suggestion=None,
                ),
            ),
            DbOperation(
                operation_id=f"operation:{task.prepared_item.item_id}:web-cache",
                operation_type="save_web_search_cache",
                payload=SaveWebSearchCachePayload(
                    normalized_menu_name=task.prepared_item.normalized_menu_name,
                    original_menu_name=task.prepared_item.raw_menu_name,
                    search_status="searched",
                    sources=list(collect.sources),
                    next_rank=collect.next_rank,
                    searched_at=dependencies.now_factory(),
                ),
            ),
        ]
        return {
            "operations": [
                PendingDbOperation(
                    item_id=task.prepared_item.item_id,
                    operation=operation,
                )
                for operation in operations
            ],
        }

    async def bayesian_node(
        state: ItemState, runtime: Runtime[RunContext]
    ) -> dict[str, Any]:
        task = state["task"]
        selected = task.knowledge.selected
        ontology: OntologyResponse = state["ontology_response"]
        web: WebAggregateResponse | None = state.get("web_aggregate_response")
        if selected is None and web is None:
            raise ItemGraphInvariantError(
                "DB Bayesian path requires selected candidate priors"
            )
        context = runtime.context.node_context(
            "item", task.prepared_item.item_id, include_store=True
        )
        warnings = list(state.get("warnings", []))
        if selected is not None and selected.priors.status == "unavailable":
            warnings.append(_slice_warning(task, "priors"))
        if task.source_reliability.status == "unavailable":
            warnings.append(_slice_warning(task, "source_reliability"))
        ingredients = (
            list(web.ingredients)
            if web is not None
            else [
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
            ]
        )
        priors = (
            BayesianPriors.model_validate(
                {
                    "status": "not_found",
                    "store": [],
                    "cluster": [],
                    "global": [],
                    "evidence": [],
                }
            )
            if web is not None
            else BayesianPriors.model_validate(
                {
                    "status": selected.priors.status,
                    "store": selected.priors.store,
                    "cluster": [],
                    "global": [],
                    "evidence": selected.priors.evidence,
                }
            )
        )
        request = BayesianRequest(
            context=context,
            menu_category_id=ontology.menu.menu_category_id,
            ingredients=ingredients,
            priors=priors,
            source_reliability=task.source_reliability,
            warnings=_dedupe_signals(warnings),
            errors=list(state.get("errors", [])),
        )
        response = await dependencies.bayesian.run(request)
        _validate_bayesian_response(request, response)
        return {
            "bayesian_response": response,
            "evidence": (
                list(selected.priors.evidence)
                if selected is not None and web is None
                else []
            ),
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
        tag_lookup: TagLookupResponse | None = state.get("tag_lookup_response")
        if tag_lookup is None:
            if exact is not None and exact.confirmations:
                raise ItemGraphInvariantError(
                    "exact confirmations require tag lookup before decision"
                )
            tag_lookup = TagLookupResponse(
                context=runtime.context.node_context(
                    "item", task.prepared_item.item_id, include_store=True
                ),
                results=[],
                warnings=list(state.get("warnings", [])),
                errors=list(state.get("errors", [])),
            )
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
            if result.source != "variant_suggested"
        ]
        proposed_variants = (
            [
                ProposedVariantIngredient(
                    ingredient=ingredient.ingredient,
                    constraint_tags=ingredient.constraint_tags,
                    role=ingredient.role,
                    source="variant_suggested",
                    evidence_refs=[
                        ontology.variant_suggestion.evidence.evidence_id
                    ],
                )
                for ingredient in ontology.variant_suggestion.suggested_ingredients
            ]
            if ontology is not None
            and ontology.variant_suggestion is not None
            else []
        )
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
            proposed_variant_ingredients=proposed_variants,
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
            no_information=state.get("route") == "no_information",
            information_status=_information_status(
                state, confirmed_results, probabilities, proposed_variants
            ),
            owner_interaction_allowed=runtime.context.store_id is not None,
            user_profile=task.user_profile,
            locale=task.locale,
            localized_names=_localized_names(
                task, confirmed_results, probabilities, proposed_variants
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
            request.no_information
            or request.information_status != "complete"
            or any(warning.forces_caution for warning in request.warnings)
            or bool(request.probability_results)
            or bool(request.proposed_variant_ingredients)
            or not request.confirmed_results
            or not request.owner_interaction_allowed
            or any(
                result.anomaly_locked
                for result in request.probability_results
            )
            or _owner_answer_status(exact, response.owner_cards) != "complete"
        ):
            raise ItemGraphInvariantError(
                "Decision returned safe while a safety restriction is active"
            )
        return {
            "decision_request": request,
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
        owner_answer_status = _owner_answer_status(
            state.get("exact_response"), decision.owner_cards
        )
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
            owner_answer_status=owner_answer_status,
            risk_ingredients=[
                ingredient.model_dump(exclude={"anomaly_locked"})
                for ingredient in decision.risk_ingredients
            ],
            matched_tags=decision.matched_tags,
            evidence_refs=decision.evidence_refs,
            message=decision.message,
            owner_cards=[_result_owner_card(card) for card in decision.owner_cards],
            warnings=decision.warnings,
            errors=decision.errors,
        )
        output = ItemAnalysisOutput(
            result=result,
            evidence=tuple(evidence),
            commands=tuple(state.get("commands", [])),
            operations=tuple(state.get("operations", [])),
            review=_review_candidate(
                state["decision_request"], decision
            ),
        )
        return {"result": result, "analysis_output": output}

    graph = StateGraph(ItemState, context_schema=RunContext)
    graph.add_node("prepare_item", prepare_item_node)
    graph.add_node("exact", exact_node)
    graph.add_node("ontology", ontology_node)
    graph.add_node("variant_review", variant_review_node)
    graph.add_node("web_search", web_collect_node)
    graph.add_node("ingredient_mapping", ingredient_mapping_node)
    graph.add_node("web_aggregate", web_aggregate_node)
    graph.add_node("web_review", web_review_node)
    graph.add_node("bayesian", bayesian_node)
    graph.add_node("tag_lookup", tag_lookup_node)
    graph.add_node("decision", decision_node)
    graph.add_node("finish_item", finish_item_node)
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
            "variant_review": "variant_review",
            "web_search": "web_search",
        },
    )
    graph.add_edge("variant_review", "bayesian")
    graph.add_conditional_edges(
        "web_search",
        web_collect_route,
        {
            "ingredient_mapping": "ingredient_mapping",
            "decision_no_information": "decision",
        },
    )
    graph.add_edge("ingredient_mapping", "web_aggregate")
    graph.add_conditional_edges(
        "web_aggregate",
        web_aggregate_route,
        {
            "web_review": "web_review",
            "decision_no_information": "decision",
        },
    )
    graph.add_edge("web_review", "bayesian")
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


def _owner_answer_status(exact: ExactFeedbackResponse | None, cards: list[Any]) -> str:
    if exact is None:
        return "none"
    has_answer = any(
        item.status in {"present", "absent"}
        for item in [*exact.confirmations, *exact.flag_confirmations]
    )
    if not has_answer:
        return "none"
    has_unanswered_target = any(
        card.ingredient is not None or card.ambiguity_flag is not None for card in cards
    )
    if exact.completeness == "complete" and not has_unanswered_target:
        return "complete"
    return "partial"


def _review_candidate(
    request: DecisionRequest, response: DecisionResponse
) -> ItemReviewCandidate | None:
    review = response.high_risk_review
    if not review.required:
        return None
    if review.reason_code is None:
        raise ItemGraphInvariantError("required high-risk review has no reason")

    found: dict[tuple[str, int | str], FoundIngredient] = {}
    for item in [
        *request.confirmed_results,
        *request.probability_results,
        *request.proposed_variant_ingredients,
    ]:
        key = _ingredient_key(item.ingredient)
        previous = found.get(key)
        tags = list(item.constraint_tags)
        if previous is not None:
            tags = list(dict.fromkeys([*previous.constraint_tags, *tags]))
        found[key] = FoundIngredient(
            ingredient=item.ingredient,
            constraint_tags=tags,
        )
    answered = frozenset(
        _ingredient_key(item.ingredient)
        for item in request.confirmed_results
        if item.status in {"present", "absent"}
    )
    return ItemReviewCandidate(
        reason_code=review.reason_code,
        found_ingredients=tuple(found.values()),
        answered_ingredient_keys=answered,
    )


def _ingredient_key(ingredient: Any) -> tuple[str, int | str]:
    if ingredient.ingredient_id is not None:
        return ("id", ingredient.ingredient_id)
    return ("name", ingredient.canonical_name)


def _result_owner_card(card: Any) -> ResultOwnerCard:
    if card.ingredient is not None:
        ingredient_id = card.ingredient.ingredient_id
        target = (
            f"ingredient-{ingredient_id}"
            if ingredient_id is not None
            else f"name-{card.ingredient.canonical_name}"
        )
    elif card.ambiguity_flag is not None:
        tags = "+".join(sorted(card.constraint_tags))
        target = f"flag-{card.ambiguity_flag}-{tags}"
    else:
        target = "menu"
    return ResultOwnerCard(
        item_id=card.item_id,
        card_id=f"card:{card.item_id}:{target}",
        menu_id=card.menu_id,
        ingredient=card.ingredient,
        ambiguity_flag=card.ambiguity_flag,
        constraint_tags=card.constraint_tags,
        question=card.question,
        reason_codes=card.reason_codes,
        evidence_refs=card.evidence_refs,
    )


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


def _validate_web_collect_response(
    request: WebCollectRequest, response: WebCollectResponse
) -> None:
    _validate_response_signals(request, response, "Web collect")
    if (
        response.menu.menu_id is not None
        or response.menu.normalized_menu_name
        != request.menu.normalized_menu_name
    ):
        raise ItemGraphInvariantError("Web collect changed menu identity")
    if response.next_rank < request.next_rank:
        raise ItemGraphInvariantError("Web collect moved next_rank backwards")
    cached_urls = {source.source_url for source in request.cached_sources}
    if any(source.source_url in cached_urls for source in response.sources):
        raise ItemGraphInvariantError("Web collect returned an already cached source")


def _validate_web_sources(sources: list[WebSource]) -> None:
    urls = [source.source_url for source in sources]
    evidence_ids = [source.evidence.evidence_id for source in sources]
    if len(urls) != len(set(urls)) or len(evidence_ids) != len(set(evidence_ids)):
        raise ItemGraphInvariantError("Web sources must have unique URLs and evidence IDs")
    for source in sources:
        evidence = source.evidence
        if (
            evidence.evidence_class != "soft"
            or evidence.source_type != "web_search"
            or evidence.verification_status != "pending_review"
            or evidence.reliability_weight is not None
        ):
            raise ItemGraphInvariantError("Web source evidence is not pending soft evidence")
        if any(item.ingredient_id is not None for item in source.ingredients):
            raise ItemGraphInvariantError(
                "Web collect sources must contain unmapped ingredient names"
            )


def _validate_ingredient_mapping_response(
    request: IngredientMappingRequest, response: IngredientMappingResponse
) -> None:
    _validate_response_signals(request, response, "Ingredient mapping")
    if [result.input_name for result in response.results] != request.ingredient_names:
        raise ItemGraphInvariantError(
            "Ingredient mapping must return every requested name in order"
        )


def _validate_web_aggregate_response(
    request: WebAggregateRequest, response: WebAggregateResponse
) -> None:
    _validate_response_signals(request, response, "Web aggregate")
    if response.menu != request.menu:
        raise ItemGraphInvariantError("Web aggregate changed menu identity")


def _unique_ingredient_names(sources: list[WebSource]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for ingredient in source.ingredients:
            if ingredient.canonical_name not in seen:
                names.append(ingredient.canonical_name)
                seen.add(ingredient.canonical_name)
    return names


def _web_corpus(source_url: str) -> str:
    domain = urlparse(source_url).netloc.lower().removeprefix("www.")
    if domain == "10000recipe.com" or domain.endswith(".10000recipe.com"):
        return "10000recipe"
    if domain == "wtable.co.kr" or domain.endswith(".wtable.co.kr"):
        return "wtable"
    if domain == "semie.cooking" or domain.endswith(".semie.cooking"):
        return "semie"
    return "web_search"


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
    proposed_variants: list[ProposedVariantIngredient],
) -> list[LocalizedName]:
    used_ids = {
        item.ingredient.ingredient_id
        for item in [*confirmed, *probabilities, *proposed_variants]
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
    proposed_variants: list[ProposedVariantIngredient],
) -> str:
    if state.get("route") == "no_information":
        return "none"
    if state.get("errors"):
        return "partial"
    if any(
        warning.forces_caution and warning.code != "store_context_missing"
        for warning in state.get("warnings", [])
    ):
        return "partial"
    if not confirmed and not probabilities and not proposed_variants:
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
