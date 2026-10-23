"""Small invariant helpers shared by canonical node contracts."""

from __future__ import annotations

from .common import NodeContext


def require_scan_context(context: NodeContext, *, allow_store: bool) -> None:
    if context.call_scope != "scan" or context.item_id is not None:
        raise ValueError("scan contract requires scan scope and null item_id")
    has_store = "store_id" in context.model_fields_set
    if allow_store and not has_store:
        raise ValueError("Judge scan context requires store_id or explicit null")
    if not allow_store and has_store:
        raise ValueError("Prepare scan context must not contain store_id, including null")


def require_item_context(context: NodeContext, *, require_store: bool = False) -> None:
    if context.call_scope != "item" or context.item_id is None:
        raise ValueError("item contract requires item scope and item_id")
    if require_store and context.store_id is None:
        raise ValueError("this node requires a positive store_id")


def ensure_unique(values: list[object], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


def ensure_refs_resolve(refs: list[str], available: set[str], field_name: str) -> None:
    missing = sorted(set(refs) - available)
    if missing:
        raise ValueError(f"{field_name} contains unresolved evidence refs: {missing}")
