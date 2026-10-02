from typing import Literal
from fastapi import APIRouter
from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.index_evidence import RecrawlCandidate
from backend.app.services.index_evidence import recrawl_candidates

router = APIRouter(prefix='/api/index', tags=['index'])


@router.get('/recrawl-candidates', response_model=SuccessResponse[list[RecrawlCandidate]])
def get_recrawl_candidates(flag: Literal['stale_robots_block', 'robots_block_current', 'stale_crawl'] = 'stale_robots_block'):
    return success_response(recrawl_candidates(flag))
