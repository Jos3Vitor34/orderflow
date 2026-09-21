from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_readiness_service
from app.schemas.health import LivenessResponse, ReadinessResponse
from app.services.health import ReadinessService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=LivenessResponse)
@router.get("/live", response_model=LivenessResponse)
def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"description": "A required dependency is unavailable"}},
)
def readiness(
    response: Response,
    service: Annotated[ReadinessService, Depends(get_readiness_service)],
) -> ReadinessResponse:
    result = service.check()
    if result.status == "not_ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
