"""FastAPI boundary for the two public Supervisor workflows."""

from __future__ import annotations

import logging
from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, status

from .contracts.external import JudgeRequest, JudgeResponse, PrepareRequest, PrepareResponse
from .hashing import ContractHashMismatch


logger = logging.getLogger(__name__)


class SupervisorApiService(Protocol):
    async def prepare(self, request: PrepareRequest) -> PrepareResponse:
        ...

    async def judge(self, request: JudgeRequest) -> JudgeResponse:
        ...


def get_supervisor_service(request: Request) -> SupervisorApiService:
    """Read the lifespan-scoped service without constructing clients per request."""

    service = getattr(request.app.state, "supervisor_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "supervisor_not_configured",
                "message": "Supervisor service is not configured.",
            },
        )
    if not callable(getattr(service, "prepare", None)) or not callable(
        getattr(service, "judge", None)
    ):
        logger.error("app.state.supervisor_service does not implement Prepare/Judge")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "supervisor_not_configured",
                "message": "Supervisor service is not configured.",
            },
        )
    return cast(SupervisorApiService, service)


router = APIRouter(prefix="/v1/analyze", tags=["supervisor"])


@router.post(
    "/prepare",
    response_model=PrepareResponse,
    response_model_exclude_none=False,
)
async def prepare(request: PrepareRequest, http_request: Request) -> PrepareResponse:
    service = get_supervisor_service(http_request)
    return PrepareResponse.model_validate(await service.prepare(request))


@router.post(
    "/judge",
    response_model=JudgeResponse,
    response_model_exclude_none=False,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Prepare payload or KnowledgeBundle hash mismatch"
        }
    },
)
async def judge(request: JudgeRequest, http_request: Request) -> JudgeResponse:
    service = get_supervisor_service(http_request)
    try:
        response = await service.judge(request)
    except ContractHashMismatch as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code, "message": str(error)},
        ) from error
    return JudgeResponse.model_validate(response)
