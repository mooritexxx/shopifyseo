from fastapi import APIRouter, HTTPException

from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.sidekick import SidekickChatPayload, SidekickChatResult
from backend.app.services.dashboard_service import sidekick_chat

router = APIRouter(prefix="/api/sidekick", tags=["sidekick"])


@router.post("/chat", response_model=SuccessResponse[SidekickChatResult])
def chat(body: SidekickChatPayload):
    try:
        data = sidekick_chat(
            body.resource_type,
            body.handle.strip(),
            [m.model_dump() for m in body.messages],
            body.client_draft,
        )
        return success_response(SidekickChatResult.model_validate(data))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
