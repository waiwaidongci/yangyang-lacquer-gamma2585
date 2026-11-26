"""Holdout scorecards and work orders for the next scheduling review."""

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_gamma_deviance

from .model import Fit, Partition
from .records import FEATURES, FIELDS, Row, is_usable_measurement


@dataclass(frozen=True)
class Score:
    name: str
    mae: float
    gamma_deviance: float


def holdout_scores(holdout: list[Row], fitted: Fit, predictions: list[float]) -> list[Score]:
    # Callers normally pass the usable partition directly.  Be tolerant of a
    # date-side list containing quarantined/disputed measurements as well.
    if len(predictions) == len(holdout):
        pairs = [
            (row, estimate)
            for row, estimate in zip(holdout, predictions, strict=True)
            if is_usable_measurement(row)
        ]
    else:
        pairs = list(zip((r for r in holdout if is_usable_measurement(r)), predictions, strict=True))
    if len(pairs) < 4:
        raise ValueError("留出日期须至少覆盖 4 条有效已完工工单，才能复算对照表现。")
    actual = [row["dry_hours"] for row, _ in pairs]
    usable_predictions = [estimate for _, estimate in pairs]
    comparators = (
        ("Gamma 回归", usable_predictions),
        ("训练集平均干燥工时", [fitted.train_mean] * len(pairs)),
        ("原排班预留工时", [row["planned_hours"] for row, _ in pairs]),
    )
    scores = []
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise"):
            for name, estimate in comparators:
                mae = float(mean_absolute_error(actual, estimate))
                deviance = float(mean_gamma_deviance(actual, estimate))
                scores.append(Score(name, mae, deviance))
    except FloatingPointError as error:
        raise ValueError("留出预测与实测的比例超出计算范围，请检查留出工艺记录。") from error
    return scores


def day_audit(split: Partition) -> list[dict]:
    subsets = {"training": split.train, "holdout": split.holdout, "excluded": split.excluded, "planned": split.planned}
    daily = {name: Counter(row["work_date"] for row in rows) for name, rows in subsets.items()}
    dates = sorted({day for counts in daily.values() for day in counts})
    return [{"work_date": day, **{name: counts[day] for name, counts in daily.items()}} for day in dates]


def planned_comparisons(planned: list[Row], fitted: Fit) -> list[dict]:
    rows = []
    for record, estimate in zip(planned, fitted.predict(planned), strict=True):
        flags = [
            FIELDS[key] for index, key in enumerate(FEATURES)
            if not fitted.feature_min[index] <= record[key] <= fitted.feature_max[index]
        ]
        gap = round(estimate - record["planned_hours"], 2) or 0.0
        rows.append({
            "id": record["id"],
            "work_date": record["work_date"],
            "prep_batch": record["prep_batch"],
            "planned_hours": record["planned_hours"],
            "estimated_hours": estimate,
            "gap_hours": gap,
            "plan_status": "复核预留" if gap > 0 else "预留覆盖估时",
            "extrapolated_features": "、".join(flags) if flags else "无",
        })
    return sorted(rows, key=lambda row: (-row["gap_hours"], row["work_date"], row["id"]))


def planned_batch_summary(planning: list[dict]) -> list[dict]:
    batches = defaultdict(list)
    for row in planning:
        batches[row["work_date"], row["prep_batch"]].append(row)
    return [
        {
            "work_date": day,
            "prep_batch": batch,
            "orders": len(rows),
            "over_plan": sum(row["gap_hours"] > 0 for row in rows),
            "largest_gap_hours": max(row["gap_hours"] for row in rows),
            "first_order": rows[0]["id"],
        }
        for (day, batch), rows in sorted(batches.items())
    ]
