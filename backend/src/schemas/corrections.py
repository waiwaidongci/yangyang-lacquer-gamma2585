from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CorrectionSubmit(BaseModel):
    # The domain layer supports several field aliases; keep the HTTP boundary
    # permissive enough for analyst exports and retry envelopes.
    model_config = ConfigDict(extra='allow')

    corrections: list[dict[str, Any]] | None = None
    changes: list[dict[str, Any]] | None = None
    items: list[dict[str, Any]] | None = None
    records: list[dict[str, Any]] | None = None
    idempotency_key: str | None = Field(default=None, max_length=160)
    batch_id: str | None = Field(default=None, max_length=120)
    batch_uid: str | None = Field(default=None, max_length=120)
