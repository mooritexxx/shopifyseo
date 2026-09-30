"""Rankings API. Weekly endpoint uses the existing app deployment access boundary."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.db import db_conn
from backend.app.schemas.common import success_response
from backend.app.services import rank_tracking as svc
from shopifyseo.rank_tracking.serp import RankError

router = APIRouter(prefix='/api/rankings', tags=['rankings'])


class KeywordRequest(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    target_url: str | None = Field(None, max_length=2048)
    grp: str | None = Field(None, max_length=100)


class EstimateRequest(BaseModel):
    keyword_ids: list[int] | None = Field(None, max_length=1000)
    max_pages: int = Field(5, ge=1, le=5)


class CheckRequest(EstimateRequest):
    request_key: str = Field(min_length=1, max_length=100)


def perform(fn, *args, conflict=False, **kwargs):
    try:
        with db_conn() as conn:
            return success_response(fn(conn, *args, **kwargs))
    except RankError as exc:
        raise HTTPException(status_code=409 if conflict else 400, detail=str(exc)) from None


@router.get('')
@router.get('/')
def rankings():
    return perform(svc.list_rankings)


@router.post('/keywords')
def add_keyword(payload: KeywordRequest):
    return perform(svc.save_keyword, **payload.model_dump())


@router.delete('/keywords/{keyword_id}')
def delete_keyword(keyword_id: int):
    return perform(svc.remove_keyword, keyword_id)


@router.get('/{keyword_id}/history')
def history(keyword_id: int):
    return perform(svc.history, keyword_id)


@router.post('/estimate')
def estimate(payload: EstimateRequest):
    return perform(svc.estimate, payload.keyword_ids, payload.max_pages)


@router.post('/check')
def check(payload: CheckRequest):
    return perform(svc.start_job, payload.keyword_ids, payload.max_pages, payload.request_key, conflict=True)


@router.post('/weekly-run')
def weekly_run(payload: EstimateRequest):
    return perform(svc.start_job, None, payload.max_pages, 'weekly-' + svc.now()[:10], weekly=True, conflict=True)
