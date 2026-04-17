from fastapi import APIRouter, HTTPException, status

from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.google_ads_lab import (
    GoogleAdsLabContextPayload,
    GoogleAdsLabInvokePayload,
    GoogleAdsLabInvokeResponse,
)
from backend.app.services.google_ads_lab_service import get_google_ads_lab_context, invoke_keyword_planning_rpc

router = APIRouter(prefix="/api/google-ads-lab", tags=["google-ads-lab"])


@router.get("/context", response_model=SuccessResponse[GoogleAdsLabContextPayload])
def google_ads_lab_context():
    return success_response(get_google_ads_lab_context())


@router.post("/invoke", response_model=SuccessResponse[GoogleAdsLabInvokeResponse])
def google_ads_lab_invoke(payload: GoogleAdsLabInvokePayload):
    try:
        return success_response(
            invoke_keyword_planning_rpc(
                rpc_method=payload.rpc_method,
                body=payload.body,
                customer_id=payload.customer_id or None,
                login_customer_id=payload.login_customer_id or None,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
