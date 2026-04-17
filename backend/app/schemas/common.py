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


def success_response(data):
    return {"ok": True, "data": data}


def error_response(code: str, message: str):
    return {"ok": False, "error": {"code": code, "message": message}}
