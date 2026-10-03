from typing import Generic, TypeVar

from pydantic import BaseModel


class ApiError(BaseModel):
    code: str
    message: str


T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    ok: bool = True
    data: T


class ErrorResponse(BaseModel):
    ok: bool = False
    error: ApiError


class PaginationMeta(BaseModel):
    """Pagination metadata for list endpoints."""

    total: int
    limit: int
    offset: int
    count: int
    has_more: bool
    next_offset: int | None
    next_cursor: str | None


class PaginatedSuccessResponse(BaseModel, Generic[T]):
    """Response envelope for paginated list endpoints.

    The `data` field remains a list for backward compatibility.
    Pagination metadata is in the `meta` field.
    """

    ok: bool = True
    data: T
    meta: PaginationMeta


def success_response(data):
    return {"ok": True, "data": data}


def paginated_response(
    data: list,
    total: int,
    limit: int,
    offset: int,
    next_cursor: str | None = None,
    has_more_override: bool | None = None,
):
    """Build a paginated response with meta information.

    Args:
        data: The list of items for the current page.
        total: Total number of items matching the filters (ignoring pagination).
        limit: The limit used for this request.
        offset: The offset used for this request.
        next_cursor: Opaque cursor for the next page, or None if no next page.
        has_more_override: If provided, use this value for has_more instead of computing it.
            Use this for cursor-based pagination where has_more is determined by fetching limit+1 rows.
    """
    count = len(data)
    if has_more_override is not None:
        has_more = has_more_override
    else:
        has_more = offset + count < total
    next_offset = offset + count if has_more else None
    return {
        "ok": True,
        "data": data,
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "count": count,
            "has_more": has_more,
            "next_offset": next_offset,
            "next_cursor": next_cursor if has_more else None,
        },
    }


def error_response(code: str, message: str):
    return {"ok": False, "error": {"code": code, "message": message}}
