from sqlalchemy import JSON, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base


class MeasurementCorrection(Base):
    __tablename__ = 'measurement_corrections'
    __table_args__ = (
        UniqueConstraint('dataset_id', 'correction_uid', name='uq_correction_dataset_uid'),
        UniqueConstraint('dataset_id', 'fingerprint', name='uq_correction_dataset_fingerprint'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), index=True)
    batch_uid: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    correction_uid: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    dry_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(20), index=True)
    detail: Mapped[str] = mapped_column(Text, default='')
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    created_at: Mapped[str] = mapped_column(String(32))


class CorrectionBatch(Base):
    __tablename__ = 'correction_batches'
    __table_args__ = (
        UniqueConstraint('dataset_id', 'idempotency_key', name='uq_correction_batch_dataset_key'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), index=True)
    batch_uid: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request: Mapped[dict] = mapped_column(JSON)
    response: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='completed')
    created_by: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    created_at: Mapped[str] = mapped_column(String(32))
