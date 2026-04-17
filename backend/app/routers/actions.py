from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.status import SyncStatusPayload
from backend.app.services.dashboard_service import start_sync, stop_sync


class SyncStartPayload(BaseModel):
    scope: str = "all"
    selected_scopes: list[str] = []
    force_refresh: bool = False


router = APIRouter(prefix="/api", tags=["actions"])


@router.post("/sync", response_model=SuccessResponse[SyncStatusPayload])
def sync_start(payload: SyncStartPayload):
    ok, message, state = start_sync(payload.scope, payload.selected_scopes or None, payload.force_refresh)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response(state)


@router.post("/sync/stop", response_model=SuccessResponse[SyncStatusPayload])
def sync_stop():
    ok, message, state = stop_sync()
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response(state)
