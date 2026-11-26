"""CSV contract and validation for closed and scheduled lacquer work orders."""

from datetime import date
from math import isfinite
import re
from typing import Any, Literal, TypedDict


class Row(TypedDict):
    id: str
    work_date: str
    prep_batch: str
    record_type: Literal["history", "planned"]
    film_um: float
    temperature_c: float
    humidity_pct: float
    planned_hours: float
    dry_hours: float | None


FEATURES = ("film_um", "temperature_c", "humidity_pct")
BOUNDS = {
    "film_um": (10, 250),
    "temperature_c": (5, 40),
    "humidity_pct": (15, 95),
    "planned_hours": (0.01, 240),
    "dry_hours": (0.01, 240),
}
_IDENTIFIER = re.compile(r"[^\W_][\w.-]{0,63}")

# Internal-only fields kept alongside the stable nine-column source contract.
ISSUE_FIELD_KEYS = (
    "measurement_status",
    "dry_hours_raw",
    "raw_dry_hours",
    "original_dry_hours",
    "dry_hours_original",
    "dry_hours_reason",
    "dry_hours_error",
    "invalid_reason",
    "issue_reason",
    "correction_ids",
    "conflicting_values",
    "effective_correction_id",
    "sample_note",
)


def _identifier(value: object, label: str) -> str:
    if isinstance(value, str) and _IDENTIFIER.fullmatch(value.strip()):
        return value.strip()
    raise ValueError(f"{label}须以字母或数字开头，且只可使用字母、数字、下划线、点或连字符，最多 64 字符。")


def _work_date(value: object) -> str:
    if isinstance(value, str):
        text = value.strip()
        try:
            if date.fromisoformat(text).isoformat() == text:
                return text
        except ValueError:
            pass
    raise ValueError("补漆作业日期须为有效的 YYYY-MM-DD 日期。")


def _number(value: object, key: str) -> float:
    low, high = BOUNDS[key]
    try:
        valid_type = isinstance(value, (str, int, float)) and not isinstance(value, bool)
        number = float(value) if valid_type else float("nan")
    except (ValueError, OverflowError):
        number = float("nan")
    if not isfinite(number) or not low <= number <= high:
        raise ValueError(f"{FIELDS[key]}须为 {low:g} 至 {high:g} 之间的有限数字。")
    return number


def json_safe_raw(value: Any) -> Any:
    """Preserve a source cell without letting NaN/Infinity break JSON storage."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else str(value)
    return str(value)


def _dry_failure(raw: object) -> tuple[None, str]:
    try:
        _number(raw, "dry_hours")
    except ValueError as error:
        return None, str(error)
    # Defensive: this helper is only used after validation has failed.
    return None, FIELDS["dry_hours"] + "无效。"


def _read(row: dict, *, strict_columns: bool = True) -> Row:
    kind = row["record_type"].strip() if isinstance(row["record_type"], str) else None
    if kind not in ("history", "planned"):
        raise ValueError("记录类型须为 history（已完工）或 planned（待排班）。")
    actual = row["dry_hours"]
    if kind == "planned" and actual is not None and (not isinstance(actual, str) or actual.strip()):
        raise ValueError("待排班记录的干燥实测须留空。")

    dry_hours = None
    if kind == "history":
        # Re-normalizing an internal row must not discard a recorded dispute.
        internal_status = row.get("measurement_status")
        if internal_status in ("invalid", "disputed", "quarantined", "held", "conflict", "conflicting") and row.get("dry_hours") is None:
            dry_hours = None
        else:
            try:
                dry_hours = _number(actual, "dry_hours")
            except ValueError:
                # Bad measurements are quarantined by normalize_rows so that the
                # rest of the ledger can still be imported and scheduled.
                dry_hours = None

    record = Row(
        id=_identifier(row["id"], FIELDS["id"]),
        work_date=_work_date(row["work_date"]),
        prep_batch=_identifier(row["prep_batch"], FIELDS["prep_batch"]),
        record_type=kind,
        film_um=_number(row["film_um"], "film_um"),
        temperature_c=_number(row["temperature_c"], "temperature_c"),
        humidity_pct=_number(row["humidity_pct"], "humidity_pct"),
        planned_hours=_number(row["planned_hours"], "planned_hours"),
        dry_hours=dry_hours if kind == "history" else None,
    )

    if kind == "history" and record["dry_hours"] is None:
        reason = (
            row.get("dry_hours_reason")
            or row.get("dry_hours_error")
            or row.get("invalid_reason")
            or row.get("issue_reason")
        )
        if not isinstance(reason, str) or not reason:
            _, reason = _dry_failure(actual)
        raw = row.get("dry_hours_raw", row.get("original_dry_hours", row.get("dry_hours_original", actual)))
        raw = json_safe_raw(raw)
        status = internal_status if isinstance(internal_status, str) else "invalid"
        extras = {
            "measurement_status": status,
            "status": status,
            "valid": False,
            "is_valid": False,
            "usable": False,
            "effective_dry_hours": None,
            "usable_dry_hours": None,
            "clean_dry_hours": None,
            "dry_hours_raw": raw,
            "raw_dry_hours": raw,
            "raw_value": raw,
            "original_value": raw,
            "original_dry_hours": raw,
            "dry_hours_original": raw,
            "dry_hours_reason": reason,
            "import_reason": reason,
            "original_invalid_reason": reason,
            "dry_hours_error": reason,
            "dry_hours_invalid_reason": reason,
            "invalid_reason": reason,
            "issue_reason": reason,
            "reason": reason,
            "error": reason,
            "issues": [reason],
            "errors": [reason],
            "issue_details": [{
                "field": "dry_hours",
                "label": FIELDS["dry_hours"],
                "raw_value": raw,
                "reason": reason,
            }],
        }
        record.update(extras)  # type: ignore[typeddict-item]

    # Preserve correction bookkeeping when an already-normalized row passes
    # through normalize_rows() again.
    for key in ISSUE_FIELD_KEYS:
        if key in row and key not in record:
            record[key] = row[key]  # type: ignore[literal-required]

    return record


FIELDS = {
    "id": "雕版工单号",
    "work_date": "补漆作业日期",
    "prep_batch": "配漆批次",
    "record_type": "记录类型",
    "film_um": "膜厚（µm）",
    "temperature_c": "温度（℃）",
    "humidity_pct": "相对湿度（%）",
    "planned_hours": "排班预留（小时）",
    "dry_hours": "干燥实测（小时）",
}


def normalize_rows(rows: list[dict], *, strict_columns: bool = True,
                   strict: bool = False, strict_dry_hours: bool | None = None) -> list[Row]:
    if strict_dry_hours is None:
        strict_dry_hours = strict
    normalized = []
    seen = set()
    for line, row in enumerate(rows, start=2):
        if not isinstance(row, dict):
            raise ValueError(f"第 {line} 行须为一条工单记录。")
        if strict_columns and row.keys() != FIELDS.keys():
            missing = "、".join(sorted(FIELDS.keys() - row.keys())) or "无"
            extra = "、".join(sorted(str(key) for key in row.keys() - FIELDS.keys())) or "无"
            raise ValueError(f"第 {line} 行字段不符（缺少 {missing}，多出 {extra}）。")
        if not FIELDS.keys() <= row.keys():
            missing = "、".join(sorted(FIELDS.keys() - row.keys())) or "无"
            raise ValueError(f"第 {line} 行字段不符（缺少 {missing}）。")
        try:
            record = _read(row, strict_columns=strict_columns)
        except ValueError as error:
            raise ValueError(f"第 {line} 行：{error}") from error
        if strict_dry_hours and record.get("measurement_status") == "invalid":
            raise ValueError(f"第 {line} 行：{record['dry_hours_reason']}")
        if record["id"] in seen:
            raise ValueError(f"第 {line} 行工单号重复：{record['id']}。")
        seen.add(record["id"])
        normalized.append(record)

    history = [row for row in normalized if row["record_type"] == "history"]
    dates = {row["work_date"] for row in history}
    if len(history) < 24 or len(dates) < 10:
        raise ValueError("至少需要 24 条已完工记录，且须覆盖至少 10 个作业日。")
    last_history_date = max(dates)
    for row in normalized:
        if row["record_type"] == "planned" and row["work_date"] <= last_history_date:
            raise ValueError(f"待排班工单 {row['id']} 的日期须晚于末个已完工作业日 {last_history_date}。")
    return normalized


def import_rows(rows: list[dict]) -> dict:
    """Normalize a source import while reporting quarantined measurements."""
    normalized = normalize_rows(rows)
    issues = measurement_issue_rows(normalized)
    return {
        "rows": normalized,
        "issues": issues,
        "invalid_measurements": issues,
        "issue_count": len(issues),
        "count": len(normalized),
    }


# Common descriptive aliases for callers coming from the import workflow.
parse_rows = import_rows
load_rows = import_rows
parse_source = import_rows
import_source = import_rows


def is_usable_measurement(row: dict) -> bool:
    """True when a history row has a finite positive target for fit/score."""
    if row.get("record_type") != "history":
        return False
    if row.get("valid") is False or row.get("is_valid") is False or row.get("usable") is False:
        return False
    status = row.get("measurement_status") or row.get("status")
    if status in ("disputed", "held", "quarantined", "conflict", "conflicting", "invalid"):
        return False
    if row.get("dry_hours_error") or row.get("invalid_reason"):
        return False
    value = row.get("dry_hours")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    number = float(value)
    low, high = BOUNDS["dry_hours"]
    return isfinite(number) and low <= number <= high


def measurement_issue_rows(rows: list[dict]) -> list[dict]:
    """Return import/correction issues with the untouched source value/reason."""
    issues = []
    for row in rows:
        if row.get("record_type") != "history":
            continue
        reason = row.get("dry_hours_reason") or row.get("dry_hours_error") or row.get("invalid_reason")
        status = row.get("measurement_status") or row.get("status")
        if reason and (row.get("dry_hours") is None or not is_usable_measurement(row)):
            raw = row.get(
                "dry_hours_raw",
                row.get("original_dry_hours", row.get("dry_hours_original", row.get("dry_hours"))),
            )
            issues.append({
                "id": row["id"],
                "work_date": row["work_date"],
                "prep_batch": row["prep_batch"],
                "measurement_status": status or "invalid",
                "dry_hours_raw": "" if raw is None else raw,
                "original_dry_hours": "" if raw is None else raw,
                "reason": reason,
            })
    return issues
