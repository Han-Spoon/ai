"""Interfaces for adapting existing ①~⑧ modules to canonical wire models."""

from __future__ import annotations

from typing import Protocol, TypeVar

RequestT = TypeVar("RequestT", contravariant=True)
ResponseT = TypeVar("ResponseT", covariant=True)


class NodeAdapter(Protocol[RequestT, ResponseT]):
    async def run(self, request: RequestT) -> ResponseT:
        """Run one node without calling any downstream node."""
        ...
