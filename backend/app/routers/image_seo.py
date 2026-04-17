from fastapi import APIRouter, HTTPException, Query, status

from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.image_seo import (
    CatalogImageSeoRow,
    ImageSeoSummary,
    ImageSeoSuggestAltRequest,
    ImageSeoSuggestAltResult,
    ProductImageSeoDraftRequest,
    ProductImageSeoDraftResult,
    ProductImageSeoListPayload,
    ProductImageSeoOptimizeRequest,
    ProductImageSeoOptimizeResult,
)
from backend.app.services.image_seo_service import (
    draft_optimize_product_image,
    list_product_image_seo_rows,
    optimize_product_image,
    suggest_catalog_image_alt_vision,
)

router = APIRouter(prefix="/api/image-seo", tags=["image-seo"])


@router.get("/product-images", response_model=SuccessResponse[ProductImageSeoListPayload])
def get_product_image_seo_list(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    missing_alt: bool | None = Query(default=None),
    weak_filename: bool | None = Query(default=None),
    status: str | None = Query(
        default=None,
        description="Filter: optimized | not_optimized (default all)",
    ),
    product_query: str = Query(default=""),
    resource_type: str | None = Query(
        default=None,
        description="Filter: product | collection | page | article | all (default all)",
    ),
    sort: str = Query(default="handle"),
    direction: str = Query(default="asc"),
):
    rt = (resource_type or "").strip().lower()
    if rt in ("", "all"):
        rt_param = None
    elif rt in {"product", "collection", "page", "article"}:
        rt_param = rt
    else:
        rt_param = None

    allowed_sort = {
        "handle",
        "title",
        "position",
        "type",
        "alt",
        "status",
        "optimize",
    }
    sort_key = sort if sort in allowed_sort else "handle"
    dir_key = direction if direction in {"asc", "desc"} else "asc"

    st = (status or "").strip().lower()
    status_param = st if st in {"optimized", "not_optimized"} else None

    items, total, summary_dict = list_product_image_seo_rows(
        limit=limit,
        offset=offset,
        missing_alt=missing_alt,
        weak_filename=weak_filename,
        status=status_param,
        product_query=product_query,
        resource_type=rt_param,
        sort=sort_key,
        direction=dir_key,
    )
    payload = ProductImageSeoListPayload(
        items=[CatalogImageSeoRow.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
        summary=ImageSeoSummary.model_validate(summary_dict),
    )
    return success_response(payload.model_dump())


@router.post("/suggest-alt", response_model=SuccessResponse[ImageSeoSuggestAltResult])
def post_suggest_image_alt(payload: ImageSeoSuggestAltRequest):
    raw = suggest_catalog_image_alt_vision(payload.model_dump())
    if not raw.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=raw.get("message") or "Suggest alt failed",
        )
    result = ImageSeoSuggestAltResult.model_validate(raw)
    return success_response(result.model_dump())


@router.post("/product-images/draft", response_model=SuccessResponse[ProductImageSeoDraftResult])
def post_product_image_draft(payload: ProductImageSeoDraftRequest):
    raw = draft_optimize_product_image(payload.model_dump())
    result = ProductImageSeoDraftResult.model_validate(raw)
    return success_response(result.model_dump())


@router.post("/product-images/optimize", response_model=SuccessResponse[ProductImageSeoOptimizeResult])
def post_optimize_product_image(payload: ProductImageSeoOptimizeRequest):
    try:
        raw = optimize_product_image(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc) or "Optimize failed",
        ) from exc

    if not raw.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=raw.get("message") or "Request failed",
        )
    result = ProductImageSeoOptimizeResult.model_validate(raw)
    return success_response(result.model_dump())
