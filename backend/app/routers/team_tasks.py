"""Authenticated API for the shared team task manager."""
from pydantic import AwareDatetime
from fastapi import APIRouter, Depends, Query
from backend.app.db import db_conn
from backend.app.schemas.team_tasks import Actor, Status, CreateTask, PatchTask, ChangeStatus, AddNote, Decision, Review
from backend.app.services import team_tasks as service
from backend.app.services.task_identity import ACTORS, authenticate

router = APIRouter(prefix='/api/tasks', tags=['team tasks'])


@router.get('/actors')
def actors(actor: str = Depends(authenticate)):
    return {'ok': True, 'data': {'current': actor, 'actors': ACTORS}}


@router.get('/events')
def events(since: AwareDatetime | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), actor: str = Depends(authenticate)):
    with db_conn() as conn:
        return {'ok': True, 'data': service.events(conn, since=since, limit=limit, offset=offset)}


@router.get('')
def listing(owner: Actor | None = None, status: Status | None = None, stale: bool = False, done_this_week: bool = False,
            limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), actor: str = Depends(authenticate)):
    with db_conn() as conn:
        return {'ok': True, 'data': service.list_tasks(conn, owner, status, stale, done_this_week, limit, offset)}


@router.post('', status_code=201)
def create(payload: CreateTask, actor: str = Depends(authenticate)):
    with db_conn() as conn:
        return {'ok': True, 'data': service.create(conn, actor, payload)}


@router.get('/{task_id}')
def detail(task_id: int, actor: str = Depends(authenticate)):
    with db_conn() as conn:
        return {'ok': True, 'data': service.get_task(conn, task_id)}


@router.get('/{task_id}/events')
def history(task_id: int, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), actor: str = Depends(authenticate)):
    with db_conn() as conn:
        return {'ok': True, 'data': service.events(conn, task_id, limit=limit, offset=offset)}


def write(task_id, actor, payload, action):
    with db_conn() as conn:
        return {'ok': True, 'data': service.mutate(conn, task_id, actor, payload, action)}


@router.patch('/{task_id}')
def update(task_id: int, payload: PatchTask, actor: str = Depends(authenticate)):
    return write(task_id, actor, payload, 'patch')


@router.post('/{task_id}/status')
def status(task_id: int, payload: ChangeStatus, actor: str = Depends(authenticate)):
    return write(task_id, actor, payload, 'status')


@router.post('/{task_id}/notes')
def note(task_id: int, payload: AddNote, actor: str = Depends(authenticate)):
    return write(task_id, actor, payload, 'note')


@router.post('/{task_id}/decision')
def decision(task_id: int, payload: Decision, actor: str = Depends(authenticate)):
    return write(task_id, actor, payload, 'decision')


@router.post('/{task_id}/review')
def review(task_id: int, payload: Review, actor: str = Depends(authenticate)):
    return write(task_id, actor, payload, 'review')
