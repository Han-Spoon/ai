"""⑦ DB Update Tool adapter matching the Supervisor ``NodeAdapter`` shape."""

from __future__ import annotations

from ai_supervisor.contracts.db_update import DbUpdateRequest, DbUpdateResponse

from .commands import build_commands


class DbUpdateTool:
    """``db_update`` node (⓪ §4-6). Returns commands; never writes."""

    async def run(self, request: DbUpdateRequest) -> DbUpdateResponse:
        return build_commands(request)
