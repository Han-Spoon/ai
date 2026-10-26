"""④ DB / Ontology Tool adapters matching the Supervisor ``NodeAdapter`` shape."""

from __future__ import annotations

from ai_supervisor.contracts.ontology import (
    OntologyRequest,
    OntologyResponse,
    TagLookupRequest,
    TagLookupResponse,
    TagLookupResult,
)

from .query import query_ontology
from .tags import lookup_tags


class OntologyTool:
    """``ontology`` node: menu query, variant check, expansion (④ §2-1)."""

    async def run(self, request: OntologyRequest) -> OntologyResponse:
        return query_ontology(request)


class TagLookupTool:
    """``tag_lookup`` node: fills risk tags for ③ confirmations before ⑧ (⓪ §2-8).

    ``role`` is ``None`` for now: the request carries no menu (recipe rows, menu name),
    and role rules need them (④ §1-2). Role feeds only the "hard to remove" note, never
    the verdict.
    """

    async def run(self, request: TagLookupRequest) -> TagLookupResponse:
        outcome = lookup_tags(
            request.ingredient_ids, request.ingredient_graph, request.context.item_id
        )
        return TagLookupResponse(
            context=request.context,
            results=[
                TagLookupResult(
                    ingredient_id=ingredient_id,
                    constraint_tags=outcome.tags[ingredient_id],
                    role=None,
                )
                for ingredient_id in request.ingredient_ids
            ],
            warnings=[*request.warnings, *outcome.warnings],
            errors=list(request.errors),
        )
