from fastapi import APIRouter

from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.dashboard import DashboardSummary, GscPeriodMode
from backend.app.services.dashboard_service import get_dashboard_summary


router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/summary", response_model=SuccessResponse[DashboardSummary])
def summary(
    gsc_period: GscPeriodMode = "rolling_30d",
    gsc_segment: str = "all",
):
    return success_response(
        get_dashboard_summary(gsc_period=gsc_period, gsc_segment=gsc_segment)
    )


@router.get("/overview/change-results", response_model=SuccessResponse[dict])
def overview_change_results():
    from backend.app.db import open_db_connection
    from backend.app.services.overview_results import change_results
    conn = open_db_connection()
    try:
        return success_response(change_results(conn))
    finally:
        conn.close()
