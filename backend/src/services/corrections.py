from collections import defaultdict
from threading import RLock

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.domain.corrections import merge_corrections
from src.domain.records import measurement_issue_rows
from src.models.correction import CorrectionBatch, MeasurementCorrection
from src.models.dataset import Dataset
from src.services.catalog import get_dataset
from src.utils.time import utc_stamp


_dataset_locks: dict[int, RLock] = defaultdict(RLock)


def _correction_dict(item: MeasurementCorrection) -> dict:
    return {
        'id': item.id,
        'database_id': item.id,
        'correction_uid': item.correction_uid,
        'idempotency_key': item.idempotency_key,
        'batch_uid': item.batch_uid,
        'order_id': item.order_id,
        'dry_hours': item.dry_hours,
        'raw_value': item.raw_value,
        'reason': item.reason or '',
        'status': item.status,
        'detail': item.detail or '',
        'fingerprint': item.fingerprint,
    }


def list_corrections(session, dataset_id: int):
    get_dataset(session, dataset_id)
    items = session.scalars(
        select(MeasurementCorrection)
        .where(MeasurementCorrection.dataset_id == dataset_id)
        .order_by(MeasurementCorrection.id)
    ).all()
    return [_correction_dict(item) for item in items]


def effective_rows(session, dataset: Dataset):
    """Reconcile immutable imported rows with all retained corrections."""
    items = session.scalars(
        select(MeasurementCorrection)
        .where(MeasurementCorrection.dataset_id == dataset.id)
        .order_by(MeasurementCorrection.id)
    ).all()
    existing = [_correction_dict(item) for item in items]
    result = merge_corrections(dataset.rows, [], existing=existing)
    return result['rows'], result['corrections']


def _state_response(dataset_id: int, effective: list[dict], records: list[dict], **extra):
    return {
        'dataset_id': dataset_id,
        'correction_count': len(records),
        'rows': effective,
        'issues': measurement_issue_rows(effective),
        'sample_ids': {
            'usable_history': [row['id'] for row in effective
                                if row.get('record_type') == 'history' and row.get('dry_hours') is not None],
        },
        'corrections': records,
        **extra,
    }


def _replay_response(prior_batch):
    return {
        **prior_batch.response,
        'created': 0,
        'created_count': 0,
        'new_count': 0,
        'replayed': True,
        'deduplicated': True,
        'duplicate': True,
        'idempotent_replay': True,
        'batch_id': prior_batch.id,
    }


def submit_corrections(session, dataset_id: int, payload, actor,
                       idempotency_key: str | None = None) -> dict:
    actor_id = getattr(actor, 'id', actor)
    if isinstance(payload, dict):
        idempotency_key = idempotency_key or payload.get('idempotency_key')
        batch_uid = payload.get('batch_uid') or payload.get('batch_id')
    else:
        batch_uid = None
    idempotency_key = idempotency_key.strip() if isinstance(idempotency_key, str) and idempotency_key.strip() else None
    batch_uid = batch_uid.strip() if isinstance(batch_uid, str) and batch_uid.strip() else None

    if idempotency_key:
        prior_batch = session.scalar(select(CorrectionBatch).where(
            CorrectionBatch.dataset_id == dataset_id,
            CorrectionBatch.idempotency_key == idempotency_key,
        ))
        if prior_batch is not None:
            if prior_batch.request != payload:
                raise ValueError('相同幂等键提交了不同补正内容，请使用新的幂等键。')
            return _replay_response(prior_batch)

    # Serialize concurrent correction batches for this dataset in one process.
    # The unique constraints below also make retries safe across processes.
    with _dataset_locks[dataset_id]:
        dataset = session.execute(
            select(Dataset).where(Dataset.id == dataset_id).with_for_update()
        ).scalar_one_or_none()
        if dataset is None:
            raise LookupError('找不到来源台账')

        prior_batch = None
        if idempotency_key:
            prior_batch = session.scalar(select(CorrectionBatch).where(
                CorrectionBatch.dataset_id == dataset_id,
                CorrectionBatch.idempotency_key == idempotency_key,
            ))
            if prior_batch is not None:
                if prior_batch.request != payload:
                    raise ValueError('相同幂等键提交了不同补正内容，请使用新的幂等键。')
                return _replay_response(prior_batch)

        existing_models = session.scalars(
            select(MeasurementCorrection).where(MeasurementCorrection.dataset_id == dataset_id)
        ).all()
        existing = [_correction_dict(item) for item in existing_models]
        result = merge_corrections(
            dataset.rows, payload, existing=existing,
            batch_uid=batch_uid, idempotency_key=idempotency_key,
        )

        created_at = utc_stamp()
        for item in result['new_corrections']:
            raw_value = item.get('raw_value')
            if not isinstance(raw_value, str):
                raw_value = '' if raw_value is None else str(raw_value)
            session.add(MeasurementCorrection(
                dataset_id=dataset_id,
                batch_uid=item.get('batch_uid') or batch_uid,
                correction_uid=item.get('correction_uid'),
                idempotency_key=item.get('idempotency_key'),
                order_id=item.get('order_id'),
                dry_hours=item.get('dry_hours'),
                raw_value=raw_value,
                reason=item.get('reason') or '',
                status=item.get('status') or 'received',
                detail=item.get('detail') or '',
                fingerprint=item['fingerprint'],
                payload=item,
                created_by=actor_id,
                created_at=created_at,
            ))

        is_replay = result['created'] == 0 and result['replayed'] > 0
        response = _state_response(
            dataset_id,
            result['rows'],
            result['corrections'],
            created=result['created'],
            replayed=is_replay,
            applied_count=len(result['applied_corrections']),
            conflicting_count=len(result['conflicting_corrections']),
            invalid_count=len(result['invalid_corrections']),
            applied_corrections=result['applied_corrections'],
            conflicting_corrections=result['conflicting_corrections'],
            invalid_corrections=result['invalid_corrections'],
        )
        response.update({
            'created_count': result['created'],
            'new_count': result['created'],
            'replayed_count': result['replayed'],
            'deduplicated': is_replay,
            'duplicate': is_replay,
            'idempotent_replay': is_replay,
        })
        if idempotency_key:
            session.add(CorrectionBatch(
                dataset_id=dataset_id,
                batch_uid=batch_uid,
                idempotency_key=idempotency_key,
                request=payload if isinstance(payload, (dict, list)) else {'payload': str(payload)},
                response=response,
                created_by=actor_id,
                created_at=created_at,
            ))

        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if idempotency_key:
                prior_batch = session.scalar(select(CorrectionBatch).where(
                    CorrectionBatch.dataset_id == dataset_id,
                    CorrectionBatch.idempotency_key == idempotency_key,
                ))
                if prior_batch is not None:
                    if prior_batch.request != payload:
                        raise ValueError('相同幂等键提交了不同补正内容，请使用新的幂等键。')
                    return _replay_response(prior_batch)
            # A concurrent identical item can win a unique fingerprint race.
            effective, records = effective_rows(session, get_dataset(session, dataset_id))
            return _state_response(
                dataset_id, effective, records,
                created=0, replayed=True, deduplicated=True, duplicate=True,
            )

        return response
