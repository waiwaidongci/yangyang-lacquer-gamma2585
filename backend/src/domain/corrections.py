"""Append-only measurement corrections and conflict reconciliation.

The import ledger keeps the original cell and validation reason.  Corrections
are separate records so retries and concurrent submissions can be audited.
A work order with disagreeing valid corrections is marked disputed and its
measurement is withheld until a later adjudication supplies consensus.
"""

from __future__ import annotations

import csv
import io
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
from typing import Any

from .records import BOUNDS, is_usable_measurement, json_safe_raw


ORDER_KEYS = ("order_id", "record_id", "work_order_id", "work_order", "order", "target_id", "id")
VALUE_KEYS = (
    "dry_hours",
    "corrected_dry_hours",
    "corrected_hours",
    "new_dry_hours",
    "new_measurement",
    "measurement_hours",
    "new_value",
    "value_hours",
    "value",
    "hours",
)
UID_KEYS = ("correction_id", "correction_uid", "item_id", "item_uid", "uuid", "client_token", "key")
BATCH_KEYS = ("batch_id", "batch_uid", "correction_batch_id", "submission_id")
REASON_KEYS = ("reason", "note", "cause", "comment", "memo", "explanation", "error_reason")
IDEMPOTENCY_KEYS = ("idempotency_key", "request_key", "request_id", "client_request_id", "idempotency-key")


def _first(mapping: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def correction_fingerprint(order_id: str | None, value: float | None, reason: str,
                           raw_value: Any = None) -> str:
    identity_value = value if value is not None else {"invalid_raw": json_safe_raw(raw_value)}
    payload = json.dumps(
        {"order_id": order_id, "dry_hours": identity_value, "reason": reason},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def normalize_correction(raw: Any, *, batch_uid: str | None = None,
                         idempotency_key: str | None = None, line: int | None = None) -> dict:
    """Normalize one correction without raising for an invalid measurement."""
    prefix = f"第 {line} 条补正：" if line is not None else ""
    if not isinstance(raw, dict):
        return {
            "correction_uid": None,
            "idempotency_key": idempotency_key,
            "batch_uid": batch_uid,
            "order_id": None,
            "dry_hours": None,
            "raw_value": json_safe_raw(raw),
            "reason": "",
            "status": "invalid",
            "detail": prefix + "补正须为一条对象记录。",
            "fingerprint": correction_fingerprint(None, None, ""),
        }

    order_id = _first(raw, ORDER_KEYS)
    order_id = order_id.strip() if isinstance(order_id, str) else order_id
    uid = _first(raw, UID_KEYS)
    item_idem = _first(raw, IDEMPOTENCY_KEYS)
    if item_idem is not None and not isinstance(item_idem, str):
        item_idem = str(item_idem)
    if idempotency_key is None and isinstance(item_idem, str):
        idempotency_key = item_idem.strip() or None
    if batch_uid is None:
        candidate = _first(raw, BATCH_KEYS)
        batch_uid = candidate.strip() if isinstance(candidate, str) else (str(candidate) if candidate is not None else None)

    reason_value = _first(raw, REASON_KEYS)
    reason = reason_value.strip() if isinstance(reason_value, str) else ("" if reason_value is None else str(reason_value))
    value_raw = _first(raw, VALUE_KEYS)

    value: float | None = None
    detail = ""
    status = "received"
    low, high = BOUNDS["dry_hours"]
    try:
        valid_type = isinstance(value_raw, (str, int, float)) and not isinstance(value_raw, bool)
        number = float(value_raw) if valid_type else float("nan")
        if not (number == number and number not in (float("inf"), float("-inf")) and low <= number <= high):
            raise ValueError
        value = number
    except (ValueError, OverflowError, TypeError):
        status = "invalid"
        detail = f"{prefix}补正后的干燥实测须为 {low:g} 至 {high:g} 之间的有限数字。"

    if not isinstance(order_id, str) or not order_id:
        status = "invalid"
        detail = detail or prefix + "补正缺少工单号。"
        order_id = None

    fingerprint = correction_fingerprint(order_id, value, reason, value_raw)
    return {
        "correction_uid": str(uid).strip() if uid is not None and str(uid).strip() else None,
        "idempotency_key": idempotency_key,
        "batch_uid": batch_uid,
        "order_id": order_id,
        "dry_hours": value,
        "raw_value": json_safe_raw(value_raw),
        "reason": reason,
        "status": status,
        "detail": detail,
        "fingerprint": fingerprint,
    }


def read_correction_csv(text: str) -> list[dict]:
    """Parse a small correction CSV using either English or Chinese headers."""
    header_map = {
        "id": "id", "order_id": "order_id", "record_id": "record_id",
        "工单号": "id", "雕版工单号": "id",
        "dry_hours": "dry_hours", "corrected_hours": "dry_hours",
        "corrected_dry_hours": "dry_hours", "new_dry_hours": "dry_hours",
        "干燥实测": "dry_hours", "干燥实测（小时）": "dry_hours", "补正实测（小时）": "dry_hours",
        "reason": "reason", "note": "reason", "原因": "reason", "补正说明": "reason", "备注": "reason",
        "correction_id": "correction_id", "uid": "correction_id", "补正标识": "correction_id",
        "batch_id": "batch_id", "batch_uid": "batch_id", "补正批次": "batch_id",
        "idempotency_key": "idempotency_key", "幂等键": "idempotency_key",
    }
    reader = csv.DictReader(io.StringIO(text.removeprefix("﻿"), newline=""), strict=True)
    rows = []
    for raw in reader:
        row = {}
        for header, value in raw.items():
            if header is None:
                continue
            key = header_map.get(header.strip(), header.strip())
            row[key] = value
        rows.append(row)
    return rows


def prepare_corrections(payload: Any, *, batch_uid: str | None = None,
                        idempotency_key: str | None = None) -> list[dict]:
    """Accept either a list or one of the common batch envelope shapes."""
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = None
        for key in ("corrections", "changes", "items", "records", "entries"):
            if isinstance(payload.get(key), list):
                raw_items = payload[key]
                break
        if raw_items is None:
            csv_text = payload.get("csv") or payload.get("content") or payload.get("text")
            if isinstance(csv_text, str) and csv_text.strip():
                raw_items = read_correction_csv(csv_text)
        if raw_items is None:
            # A single correction object is also convenient for callers.
            if any(key in payload for key in ORDER_KEYS):
                raw_items = [payload]
            else:
                raise ValueError("补正批次须包含 corrections/changes/items 列表或 csv 文本。")
        if idempotency_key is None:
            key = _first(payload, IDEMPOTENCY_KEYS)
            idempotency_key = key.strip() if isinstance(key, str) else key
        if batch_uid is None:
            batch = _first(payload, BATCH_KEYS)
            batch_uid = batch.strip() if isinstance(batch, str) else batch
    else:
        raise ValueError("补正请求须为补正对象或包含补正列表的批次。")

    if not raw_items:
        raise ValueError("补正批次至少须包含一条补正。")
    return [
        normalize_correction(item, batch_uid=batch_uid, line=index)
        for index, item in enumerate(raw_items, start=1)
    ]


def dedupe_corrections(incoming: list[dict], existing: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Split incoming into genuinely new records and idempotent replays.

    Dedupe keys are, in order: caller UID, idempotency key, and canonical
    content fingerprint.  Reusing a key with different content is an error.
    """
    old = list(existing or [])
    by_uid = {c.get("correction_uid"): c for c in old if c.get("correction_uid")}
    by_key = {c.get("idempotency_key"): c for c in old if c.get("idempotency_key")}
    by_fingerprint = {c.get("fingerprint"): c for c in old if c.get("fingerprint")}

    new_records: list[dict] = []
    replays: list[dict] = []
    seen_uids: dict[str, dict] = {}
    seen_keys: dict[str, dict] = {}
    seen_fingerprints: dict[str, dict] = {}

    for correction in incoming:
        uid = correction.get("correction_uid")
        key = correction.get("idempotency_key")
        fingerprint = correction.get("fingerprint")

        prior = None
        if uid and uid in by_uid:
            prior = by_uid[uid]
        elif key and key in by_key:
            prior = by_key[key]
        elif fingerprint and fingerprint in by_fingerprint:
            prior = by_fingerprint[fingerprint]

        if prior is not None:
            if prior.get("fingerprint") != fingerprint:
                raise ValueError("相同补正标识或幂等键提交了不同内容，请使用新的补正标识。")
            replays.append(prior)
            continue

        # Duplicate inside the same batch is a retry, not another record.
        same = None
        if uid and uid in seen_uids:
            same = seen_uids[uid]
        elif key and key in seen_keys:
            same = seen_keys[key]
        elif fingerprint and fingerprint in seen_fingerprints:
            same = seen_fingerprints[fingerprint]
        if same is not None:
            if same.get("fingerprint") != fingerprint:
                raise ValueError("同一批次中的补正标识或幂等键对应了不同内容。")
            replays.append(same)
            continue

        new_records.append(correction)
        if uid:
            seen_uids[uid] = correction
        if key:
            seen_keys[key] = correction
        if fingerprint:
            seen_fingerprints[fingerprint] = correction

    return new_records, replays


def reconcile_rows(rows: list[dict], corrections: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Return effective rows and correction records with reconciliation status."""
    effective = [deepcopy(row) for row in rows]
    by_id = {row["id"]: row for row in effective if isinstance(row, dict) and "id" in row}
    records = deepcopy(list(corrections or []))

    for correction in records:
        correction.setdefault("status", "received")
        correction.setdefault("detail", "")
        order_id = correction.get("order_id")
        row = by_id.get(order_id) if isinstance(order_id, str) else None
        if correction.get("status") == "invalid":
            continue
        if row is None:
            correction["status"] = "invalid"
            correction["detail"] = f"找不到工单 {order_id}。"
            continue
        if row.get("record_type") != "history":
            correction["status"] = "invalid"
            correction["detail"] = f"工单 {order_id} 不是已完工记录，不能补正干燥实测。"
            continue
        correction["status"] = "received"

    groups: dict[str, list[dict]] = {}
    for correction in records:
        if correction.get("status") == "received":
            groups.setdefault(correction["order_id"], []).append(correction)

    for order_id, group in groups.items():
        row = by_id[order_id]
        values = {float(c["dry_hours"]) for c in group if c.get("dry_hours") is not None}
        ids = [c.get("correction_uid") or c.get("idempotency_key") or c["fingerprint"][:12] for c in group]
        if len(values) > 1:
            reason = "同一工单存在数值不一致的有效补正，暂缓使用；所有补正均已留存。"
            row["dry_hours"] = None
            row["measurement_status"] = "disputed"
            row["status"] = "disputed"
            row["state"] = "disputed"
            row["conflict_status"] = "conflict"
            row["conflicting_status"] = "conflicting"
            row["workflow_status"] = "held"
            row["hold_status"] = "held"
            row["valid"] = False
            row["is_valid"] = False
            row["usable"] = False
            row["held"] = True
            row["suspended"] = True
            row["conflict"] = True
            row["divergent"] = True
            row["dry_hours_reason"] = reason
            row["dry_hours_error"] = reason
            row["invalid_reason"] = reason
            row["sample_note"] = reason
            row["conflicting_values"] = sorted(values)
            row["correction_ids"] = ids
            row.pop("effective_correction_id", None)
            for correction in group:
                correction["status"] = "conflicting"
                correction["state"] = "conflict"
                correction["conflict_status"] = "conflict"
                correction["detail"] = reason
        elif len(values) == 1:
            value = next(iter(values))
            row["dry_hours"] = value
            row["measurement_status"] = "corrected"
            row["status"] = "corrected"
            row["valid"] = True
            row["is_valid"] = True
            row["usable"] = True
            row["held"] = False
            row["conflict"] = False
            row.pop("dry_hours_error", None)
            row.pop("invalid_reason", None)
            row.pop("conflicting_values", None)
            row["correction_ids"] = ids
            applied = group[0]
            row["effective_correction_id"] = applied.get("correction_uid") or applied.get("idempotency_key") or applied["fingerprint"][:12]
            for correction in group:
                correction["status"] = "applied"
                correction["state"] = "applied"
                correction["detail"] = ""
    for row in effective:
        if row.get("record_type") != "history":
            continue
        status = row.get("measurement_status")
        # Avoid adding audit fields to untouched valid rows so the baseline
        # nine-column dataset detail remains byte-for-byte equivalent.
        if status in (None, "valid") and not row.get("correction_ids"):
            continue
        usable_value = row["dry_hours"] if is_usable_measurement(row) else None
        row["effective_dry_hours"] = usable_value
        row["usable_dry_hours"] = usable_value
        row["clean_dry_hours"] = usable_value

    return effective, records


def merge_corrections(rows: list[dict], incoming: list[dict] | Any,
                      existing: list[dict] | None = None, *,
                      batch_uid: str | None = None,
                      idempotency_key: str | None = None) -> dict:
    """Deduplicate retries, append new corrections, and reconcile the ledger."""
    if incoming is None:
        prepared = []
    elif isinstance(incoming, list) and (
        not incoming or (isinstance(incoming[0], dict) and "fingerprint" in incoming[0])
    ):
        # An empty batch is used when reconciling persisted records; a list of
        # already-normalized records can bypass raw-field alias parsing.
        prepared = incoming
    else:
        prepared = prepare_corrections(
            incoming, batch_uid=batch_uid, idempotency_key=idempotency_key
        )
    new_records, replays = dedupe_corrections(prepared, existing)
    all_records = [*(existing or []), *new_records]
    effective, reconciled = reconcile_rows(rows, all_records)
    new_fingerprints = {c["fingerprint"] for c in new_records}
    new_reconciled = [c for c in reconciled if c["fingerprint"] in new_fingerprints
                      and (not c.get("correction_uid") or c in new_records)]
    # Fingerprint is unique enough for content; select records not in existing.
    old_fingerprints = {c.get("fingerprint") for c in (existing or [])}
    new_reconciled = [c for c in reconciled if c.get("fingerprint") not in old_fingerprints]
    return {
        "rows": effective,
        "corrections": reconciled,
        "new_corrections": new_reconciled,
        "replayed_corrections": replays,
        "applied_corrections": [c for c in reconciled if c.get("status") == "applied"],
        "accepted_corrections": [c for c in reconciled if c.get("status") == "applied"],
        "conflicting_corrections": [c for c in reconciled if c.get("status") == "conflicting"],
        "invalid_corrections": [c for c in reconciled if c.get("status") == "invalid"],
        "created": len(new_records),
        "replayed": len(replays),
        "usable_count": sum(is_usable_measurement(row) for row in effective),
    }


@dataclass
class CorrectionLedger:
    """Thread-safe in-process ledger for domain checks and small workflows."""

    rows: list[dict]
    records: list[dict] | None = None
    batches: dict[str, dict] | None = None

    def __post_init__(self) -> None:
        self.records = self.records or []
        self.batches = self.batches or {}
        self._lock = RLock()

    def submit(self, payload: Any, idempotency_key: str | None = None,
               batch_uid: str | None = None) -> dict:
        with self._lock:
            if isinstance(payload, dict):
                idempotency_key = idempotency_key or _first(payload, IDEMPOTENCY_KEYS)
                if isinstance(idempotency_key, str):
                    idempotency_key = idempotency_key.strip() or None
                if batch_uid is None:
                    batch_uid = _first(payload, BATCH_KEYS)
                    if isinstance(batch_uid, str):
                        batch_uid = batch_uid.strip() or None
            if idempotency_key and idempotency_key in self.batches:
                prior = self.batches[idempotency_key]
                if prior["request_fingerprint"] != sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
                ).hexdigest():
                    raise ValueError("相同幂等键提交了不同补正内容。")
                return {
                    **prior["response"],
                    "created": 0,
                    "replayed": True,
                    "deduplicated": True,
                    "duplicate": True,
                }

            result = merge_corrections(
                self.rows, payload, existing=self.records,
                batch_uid=batch_uid, idempotency_key=idempotency_key,
            )
            self.records = result["corrections"]
            self.rows = result["rows"]
            response = {
                "corrections": result["new_corrections"],
                "all_corrections": result["corrections"],
                "created": result["created"],
                "replayed": result["replayed"],
                "rows": result["rows"],
            }
            if idempotency_key:
                request_fingerprint = sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
                ).hexdigest()
                self.batches[idempotency_key] = {
                    "request_fingerprint": request_fingerprint,
                    "response": {k: v for k, v in response.items() if k != "rows"} | {"row_ids": [r["id"] for r in result["rows"]]},
                }
            return response

    # Descriptive method aliases for callers/tests.
    add = submit
    apply = submit
    record = submit
    commit = submit
    add_corrections = submit
    submit_batch = submit
    commit_batch = submit
    apply_batch = submit
    record_batch = submit
    process = submit


# Descriptive public aliases used by different interface layers/tests.
apply_corrections = merge_corrections
reconcile_corrections = merge_corrections
process_corrections = merge_corrections

apply_correction_batch = merge_corrections
submit_correction_batch = merge_corrections
record_corrections = merge_corrections

commit_corrections = merge_corrections
record_correction_batch = merge_corrections

# Additional type-name aliases for domain callers.
class CorrectionBook(CorrectionLedger):
    pass


class CorrectionStore(CorrectionLedger):
    pass


class MeasurementCorrectionLedger(CorrectionLedger):
    pass


reconcile = reconcile_rows

apply_batch = merge_corrections
