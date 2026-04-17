from fastapi import APIRouter, Query

from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.status import AiStatusPayload, AiStopRequestPayload, StoreInfoPayload, SyncStatusPayload
from backend.app.services.dashboard_service import get_ai_status, get_sync_status, stop_ai
from backend.app.services.store_info_service import get_store_info


router = APIRouter(prefix="/api", tags=["status"])


@router.get("/sync-status", response_model=SuccessResponse[SyncStatusPayload])
def sync_status():
    return success_response(get_sync_status())


@router.get("/ai-status", response_model=SuccessResponse[AiStatusPayload])
def ai_status(job_id: str = Query(default="")):
    return success_response(get_ai_status(job_id))


@router.post("/ai-stop", response_model=SuccessResponse[AiStatusPayload])
def ai_stop(payload: AiStopRequestPayload):
    _, _, state = stop_ai(payload.job_id)
    return success_response(state)


@router.get("/store-info", response_model=SuccessResponse[StoreInfoPayload])
def store_info():
    return success_response(get_store_info())
