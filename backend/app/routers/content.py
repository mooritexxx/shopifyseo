from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from backend.app.routers import field_regen_errors
from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.dashboard import GscPeriodMode
from backend.app.schemas.content import ContentDetailPayload, ContentListPayload, ContentUpdatePayload
from backend.app.schemas.product import FieldRegenerateRequest, FieldRegenerateResult, ProductActionResult, ProductInspectionLinkPayload
from backend.app.services.content_service import (
    get_content_detail,
    get_content_inspection_link as get_object_inspection_link,
    list_content,
    save_all_collection_meta_to_shopify,
    save_all_page_meta_to_shopify,
    update_content,
)
from backend.app.services.dashboard_service import (
    refresh_object,
    regenerate_object_field,
    start_object_ai,
    start_object_field_regeneration,
)


router = APIRouter(prefix="/api", tags=["content"])

VALID_KINDS = {"collections", "pages"}


class RefreshPayload(BaseModel):
    step: str | None = None


def _handle_update_error(ok: bool, message: str, not_found_message: str) -> None:
    if not ok:
        if message == not_found_message:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)


def _check_kind(kind: str) -> None:
    if kind not in VALID_KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown content type: {kind}")


@router.get("/collections", response_model=SuccessResponse[ContentListPayload])
def list_collections(
    query: str = Query(default=""),
    sort: str = Query(default="score"),
    direction: str = Query(default="desc"),
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    focus: str | None = Query(default=None, description="missing_meta"),
):
    return success_response(
        list_content("collection", query=query, sort=sort, direction=direction, limit=limit, offset=offset, focus=focus)
    )


@router.get("/pages", response_model=SuccessResponse[ContentListPayload])
def list_pages(
    query: str = Query(default=""),
    sort: str = Query(default="score"),
    direction: str = Query(default="desc"),
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    focus: str | None = Query(default=None, description="missing_meta"),
):
    return success_response(
        list_content("page", query=query, sort=sort, direction=direction, limit=limit, offset=offset, focus=focus)
    )


@router.get("/collections/{handle}", response_model=SuccessResponse[ContentDetailPayload])
def collection_detail(handle: str, gsc_period: GscPeriodMode = "mtd"):
    detail = get_content_detail("collection", handle, gsc_period=gsc_period)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    return success_response(detail)


@router.get("/pages/{handle}", response_model=SuccessResponse[ContentDetailPayload])
def page_detail(handle: str, gsc_period: GscPeriodMode = "mtd"):
    detail = get_content_detail("page", handle, gsc_period=gsc_period)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    return success_response(detail)


@router.post("/collections/{handle}/update", response_model=SuccessResponse[ProductActionResult])
def update_collection(handle: str, payload: ContentUpdatePayload, gsc_period: GscPeriodMode = "mtd"):
    ok, message = update_content("collection", handle, payload.model_dump())
    _handle_update_error(ok, message, "Collection not found")
    detail = get_content_detail("collection", handle, gsc_period=gsc_period)
    return success_response({"message": message, "result": detail})


@router.post("/pages/{handle}/update", response_model=SuccessResponse[ProductActionResult])
def update_page(handle: str, payload: ContentUpdatePayload, gsc_period: GscPeriodMode = "mtd"):
    ok, message = update_content("page", handle, payload.model_dump())
    _handle_update_error(ok, message, "Page not found")
    detail = get_content_detail("page", handle, gsc_period=gsc_period)
    return success_response({"message": message, "result": detail})


@router.post("/collections/{handle}/inspection-link", response_model=SuccessResponse[ProductInspectionLinkPayload])
def collection_inspection_link(handle: str):
    ok, href = get_object_inspection_link("collection", handle)
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=href)
    return success_response({"href": href})


@router.post("/pages/{handle}/inspection-link", response_model=SuccessResponse[ProductInspectionLinkPayload])
def page_inspection_link(handle: str):
    ok, href = get_object_inspection_link("page", handle)
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=href)
    return success_response({"href": href})


@router.post("/collections/{handle}/refresh", response_model=SuccessResponse[ProductActionResult])
def refresh_collection(handle: str, payload: RefreshPayload | None = None, gsc_period: GscPeriodMode = "mtd"):
    step = payload.step if payload else None
    ok, result = refresh_object("collection", handle, step, gsc_period=gsc_period)
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result.get("message", "Refresh failed"))
    return success_response(result)


@router.post("/pages/{handle}/refresh", response_model=SuccessResponse[ProductActionResult])
def refresh_page(handle: str, payload: RefreshPayload | None = None, gsc_period: GscPeriodMode = "mtd"):
    step = payload.step if payload else None
    ok, result = refresh_object("page", handle, step, gsc_period=gsc_period)
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result.get("message", "Refresh failed"))
    return success_response(result)


@router.post("/collections/{handle}/generate-ai", response_model=SuccessResponse[ProductActionResult])
def generate_collection_ai(handle: str):
    ok, message, state = start_object_ai("collection", handle)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response({"message": message, "state": state})


@router.post("/pages/{handle}/generate-ai", response_model=SuccessResponse[ProductActionResult])
def generate_page_ai(handle: str):
    ok, message, state = start_object_ai("page", handle)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response({"message": message, "state": state})


@router.post("/collections/{handle}/regenerate-field", response_model=SuccessResponse[FieldRegenerateResult])
def regenerate_collection_field(handle: str, payload: FieldRegenerateRequest):
    with field_regen_errors():
        result = regenerate_object_field("collection", handle, payload.field, payload.accepted_fields)
        return success_response(result)


@router.post("/pages/{handle}/regenerate-field", response_model=SuccessResponse[FieldRegenerateResult])
def regenerate_page_field(handle: str, payload: FieldRegenerateRequest):
    with field_regen_errors():
        result = regenerate_object_field("page", handle, payload.field, payload.accepted_fields)
        return success_response(result)


@router.post("/collections/{handle}/regenerate-field/start", response_model=SuccessResponse[ProductActionResult])
def regenerate_collection_field_start(handle: str, payload: FieldRegenerateRequest):
    with field_regen_errors():
        ok, message, state = start_object_field_regeneration("collection", handle, payload.field, payload.accepted_fields)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response({"message": message, "state": state})


@router.post("/pages/{handle}/regenerate-field/start", response_model=SuccessResponse[ProductActionResult])
def regenerate_page_field_start(handle: str, payload: FieldRegenerateRequest):
    with field_regen_errors():
        ok, message, state = start_object_field_regeneration("page", handle, payload.field, payload.accepted_fields)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response({"message": message, "state": state})


@router.post("/collections/save-meta", response_model=SuccessResponse[ProductActionResult])
def save_collection_meta():
    ok, message, result = save_all_collection_meta_to_shopify()
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)
    return success_response({"message": message, "result": result})


@router.post("/pages/save-meta", response_model=SuccessResponse[ProductActionResult])
def save_page_meta():
    ok, message, result = save_all_page_meta_to_shopify()
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)
    return success_response({"message": message, "result": result})
