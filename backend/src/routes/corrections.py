from typing import Any

from fastapi import APIRouter, Header, Query

from src.controllers.dependencies import Actor, Db, Writer
from src.models.run import Run
from src.services import corrections

router = APIRouter(tags=['corrections'])


@router.post('/datasets/{dataset_id}/corrections', status_code=201)
@router.post('/datasets/{dataset_id}/correction', status_code=201)
@router.post('/datasets/{dataset_id}/measurement-corrections', status_code=201)
@router.post('/datasets/{dataset_id}/dry-hour-corrections', status_code=201)
def submit_dataset_corrections(
    dataset_id: int,
    payload: dict[str, Any] | list[Any],
    session: Db,
    actor: Writer,
    idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'),
    x_idempotency_key: str | None = Header(default=None, alias='X-Idempotency-Key'),
    key: str | None = Query(default=None, alias='idempotency_key'),
):
    return corrections.submit_corrections(
        session, dataset_id, payload, actor.id,
        idempotency_key or x_idempotency_key or key,
    )


@router.get('/datasets/{dataset_id}/corrections')
@router.get('/datasets/{dataset_id}/correction')
@router.get('/datasets/{dataset_id}/measurement-corrections')
def dataset_corrections(dataset_id: int, session: Db, actor: Actor):
    return corrections.list_corrections(session, dataset_id)


@router.post('/runs/{run_id}/corrections', status_code=201)
def submit_run_corrections(
    run_id: int,
    payload: dict[str, Any] | list[Any],
    session: Db,
    actor: Writer,
    idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'),
    x_idempotency_key: str | None = Header(default=None, alias='X-Idempotency-Key'),
    key: str | None = Query(default=None, alias='idempotency_key'),
):
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError('找不到运行')
    return corrections.submit_corrections(
        session, run.dataset_id, payload, actor.id,
        idempotency_key or x_idempotency_key or key,
    )


@router.get('/runs/{run_id}/corrections')
def run_corrections(run_id: int, session: Db, actor: Actor):
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError('找不到运行')
    return corrections.list_corrections(session, run.dataset_id)


@router.post('/corrections', status_code=201)
@router.post('/correction', status_code=201)
@router.post('/measurement-corrections', status_code=201)
def submit_corrections(
    payload: dict[str, Any],
    session: Db,
    actor: Writer,
    idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'),
    x_idempotency_key: str | None = Header(default=None, alias='X-Idempotency-Key'),
    key: str | None = Query(default=None, alias='idempotency_key'),
):
    dataset_id = payload.get('dataset_id')
    if not isinstance(dataset_id, int):
        raise ValueError('补正请求须包含整数 dataset_id。')
    return corrections.submit_corrections(
        session, dataset_id, payload, actor.id,
        idempotency_key or x_idempotency_key or key,
    )
