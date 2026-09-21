from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_statistics_service, require_viewer
from app.schemas.statistics import StatisticsOverviewResponse
from app.services.statistics import InvalidStatisticsPeriodError, StatisticsService

router = APIRouter(prefix="/statistics", tags=["statistics"])


@router.get(
    "/overview",
    response_model=StatisticsOverviewResponse,
    dependencies=[Depends(require_viewer)],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Insufficient permissions"},
        422: {"description": "Invalid timezone-aware half-open interval"},
    },
)
def statistics_overview(
    service: Annotated[StatisticsService, Depends(get_statistics_service)],
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> StatisticsOverviewResponse:
    try:
        return service.overview(start=start, end=end)
    except InvalidStatisticsPeriodError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "start and end must include a timezone, and start must not be after end"
            ),
        ) from exc
