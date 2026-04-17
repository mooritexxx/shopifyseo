from fastapi import APIRouter, HTTPException, Query, status

from backend.app.routers import field_regen_errors
from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.dashboard import GscPeriodMode
from backend.app.schemas.product import (
    FieldRegenerateRequest,
    FieldRegenerateResult,
    ProductActionResult,
    ProductDetailPayload,
    ProductInspectionLinkPayload,
    ProductListPayload,
    ProductRefreshRequest,
    ProductUpdatePayload,
)
from backend.app.services.product_service import (
    get_product_detail,
    get_product_inspection_link,
    list_products,
    refresh_product,
    regenerate_product_field,
    start_product_field_regeneration,
    start_product_ai,
    update_product,
)


router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=SuccessResponse[ProductListPayload])
def products(
    query: str = Query(default=""),
    sort: str = Query(default="score"),
    direction: str = Query(default="desc"),
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    focus: str | None = Query(default=None, description="missing_meta | thin_body"),
):
    return success_response(
        list_products(query=query, sort=sort, direction=direction, limit=limit, offset=offset, focus=focus)
    )


@router.get("/{handle}", response_model=SuccessResponse[ProductDetailPayload])
def product_detail(handle: str, gsc_period: GscPeriodMode = "mtd"):
    detail = get_product_detail(handle, gsc_period=gsc_period)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return success_response(detail)


@router.post("/{handle}/refresh", response_model=SuccessResponse[ProductActionResult])
def refresh_product_route(handle: str, payload: ProductRefreshRequest, gsc_period: GscPeriodMode = "mtd"):
    ok, result = refresh_product(handle, payload.step, gsc_period=gsc_period)
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["message"])
    return success_response(result)


@router.post("/{handle}/generate-ai", response_model=SuccessResponse[ProductActionResult])
def generate_product_ai(handle: str):
    ok, message, state = start_product_ai(handle)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response({"message": message, "state": state})


@router.post("/{handle}/regenerate-field", response_model=SuccessResponse[FieldRegenerateResult])
def regenerate_field_route(handle: str, payload: FieldRegenerateRequest):
    with field_regen_errors():
        result = regenerate_product_field(handle, payload.field, payload.accepted_fields)
        return success_response(result)


@router.post("/{handle}/regenerate-field/start", response_model=SuccessResponse[ProductActionResult])
def regenerate_field_start_route(handle: str, payload: FieldRegenerateRequest):
    with field_regen_errors():
        ok, message, state = start_product_field_regeneration(handle, payload.field, payload.accepted_fields)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response({"message": message, "state": state})


@router.post("/{handle}/update", response_model=SuccessResponse[ProductActionResult])
def update_product_route(handle: str, payload: ProductUpdatePayload, gsc_period: GscPeriodMode = "mtd"):
    ok, message = update_product(handle, payload.model_dump())
    if not ok:
        if message == "Product not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)
    detail = get_product_detail(handle, gsc_period=gsc_period)
    return success_response({"message": message, "result": detail})


@router.post("/{handle}/inspection-link", response_model=SuccessResponse[ProductInspectionLinkPayload])
def inspection_link_route(handle: str):
    ok, href = get_product_inspection_link(handle)
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=href)
    return success_response({"href": href})
