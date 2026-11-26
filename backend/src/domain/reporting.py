"""Stable result keys and field labels shared by the UI and CSV export."""

from math import exp

from .diagnostics import day_audit, holdout_scores, planned_batch_summary, planned_comparisons
from .model import Fit, Partition
from .records import FEATURES, FIELDS, measurement_issue_rows


ORDER_LABELS = {key: FIELDS[key] for key in ("id", "work_date", "prep_batch")}
ESTIMATE_LABEL = "Gamma 估时（小时）"


def _table(key: str, title: str, labels: dict, rows: list[dict]) -> dict:
    return {"key": key, "title": title, "columns": list(labels), "labels": labels, "rows": rows}


def build_report(split: Partition, fitted: Fit, corrections: list[dict] | None = None) -> dict:
    estimates = fitted.predict(split.holdout)
    scores = holdout_scores(split.holdout, fitted, estimates)
    planning = planned_comparisons(split.planned, fitted)
    over_plan = sum(row["gap_hours"] > 0 for row in planning)
    out_of_range = sum(row["extrapolated_features"] != "无" for row in planning)

    comparisons = [{"method": s.name, "mae_hours": s.mae, "gamma_deviance": s.gamma_deviance} for s in scores]
    residuals = [
        {
            "id": row["id"],
            "work_date": row["work_date"],
            "prep_batch": row["prep_batch"],
            "dry_hours": row["dry_hours"],
            "estimated_hours": estimate,
            "residual_hours": row["dry_hours"] - estimate,
            "planned_hours": row["planned_hours"],
        }
        for row, estimate in zip(split.holdout, estimates, strict=True)
    ]
    training_orders = [
        {
            "id": row["id"],
            "work_date": row["work_date"],
            "prep_batch": row["prep_batch"],
            "dry_hours": row["dry_hours"],
            "planned_hours": row["planned_hours"],
        }
        for row in split.train
    ]
    issue_candidates = split.all_history
    if not issue_candidates:
        unique_rows = {}
        for row in [
            *split.train_all, *split.holdout_all, *split.excluded,
            *split.invalid, *split.train, *split.holdout,
        ]:
            unique_rows.setdefault(row["id"], row)
        issue_candidates = list(unique_rows.values())
    source_issues = measurement_issue_rows(issue_candidates)
    train_ids = {row["id"] for row in split.train}
    holdout_ids = {row["id"] for row in split.holdout}
    excluded_ids = {row["id"] for row in split.excluded}
    holdout_date_ids = {row["id"] for row in split.holdout_all}
    assignment_rows = []
    assignment_history = split.all_history or issue_candidates
    for row in [*assignment_history, *split.planned]:
        if row.get("record_type") == "planned":
            assignment = "planned"
            measurement_status = "not_measured"
        elif row["id"] in excluded_ids:
            assignment = "batch_excluded"
            measurement_status = row.get("measurement_status", "valid")
        elif row["id"] in holdout_date_ids:
            assignment = "holdout"
            measurement_status = row.get("measurement_status", "valid")
        else:
            assignment = "training"
            measurement_status = row.get("measurement_status", "valid")
        assignment_rows.append({
            "id": row["id"],
            "work_date": row["work_date"],
            "prep_batch": row["prep_batch"],
            "assignment": assignment,
            "measurement_status": measurement_status,
            "used_for_fit": row["id"] in train_ids,
            "used_for_score": row["id"] in holdout_ids,
        })
    coefficients = [
        {
            "feature": FIELDS[key],
            "log_coefficient": float(fitted.model.coef_[i]),
            "mean_multiplier": exp(float(fitted.model.coef_[i])),
            "training_sd": float(fitted.scaler.var_[i] ** 0.5),
            "training_min": float(fitted.feature_min[i]),
            "training_max": float(fitted.feature_max[i]),
        }
        for i, key in enumerate(FEATURES)
    ]
    excluded_orders = [{key: row[key] for key in ORDER_LABELS} for row in split.excluded]
    tables = [
        _table("holdout_comparison", "留出表现与排班基准", {
            "method": "对照口径", "mae_hours": "平均绝对误差（小时）", "gamma_deviance": "平均 Gamma 偏差",
        }, comparisons),
        _table("planned_orders", "待排班工单估时对照", {
            **ORDER_LABELS, "planned_hours": FIELDS["planned_hours"], "estimated_hours": ESTIMATE_LABEL,
            "gap_hours": "估时减预留（小时）", "plan_status": "排班标记", "extrapolated_features": "超训练范围的条件",
        }, planning),
        _table("planned_batches", "待排班配漆批次汇总", {
            "work_date": FIELDS["work_date"], "prep_batch": FIELDS["prep_batch"], "orders": "工单数",
            "over_plan": "超预留工单数", "largest_gap_hours": "最大工单缺口（小时）", "first_order": "最大缺口对应工单",
        }, planned_batch_summary(planning)),
        _table("training_orders", "训练工单样本", {
            **ORDER_LABELS, "dry_hours": FIELDS["dry_hours"], "planned_hours": FIELDS["planned_hours"],
        }, training_orders),
        _table("holdout_orders", "留出工单残差", {
            **ORDER_LABELS, "dry_hours": FIELDS["dry_hours"], "estimated_hours": ESTIMATE_LABEL,
            "residual_hours": "实测减估时（小时）", "planned_hours": FIELDS["planned_hours"],
        }, residuals),
        *([
            _table("measurement_issues", "异常实测原值与原因", {
                **ORDER_LABELS, "measurement_status": "处理状态", "dry_hours_raw": "导入原值",
                "reason": "不入模原因",
            }, source_issues),
        ] if source_issues else []),
        *([
            _table("measurement_corrections", "干燥实测补正记录", {
                "order_id": FIELDS["id"], "correction_uid": "补正标识", "batch_uid": "补正批次",
                "dry_hours": "补正实测（小时）", "raw_value": "提交原值", "reason": "补正说明",
                "status": "入账状态", "detail": "状态说明",
            }, [
                {
                    "order_id": item.get("order_id") or item.get("id") or item.get("record_id"),
                    "correction_uid": item.get("correction_uid") or item.get("correction_id") or item.get("idempotency_key") or "",
                    "batch_uid": item.get("batch_uid") or item.get("batch_id") or "",
                    "dry_hours": item.get("dry_hours") if item.get("dry_hours") is not None else item.get("corrected_hours"),
                    "raw_value": "" if item.get("raw_value") is None else item.get("raw_value"),
                    "reason": item.get("reason") or item.get("note") or "",
                    "status": item.get("status") or "received",
                    "detail": item.get("detail") or "",
                }
                for item in corrections
            ]),
        ] if corrections else []),
        _table("day_split", "作业日切分台账", {
            "work_date": FIELDS["work_date"], "training": "训练工单", "holdout": "留出工单",
            "excluded": "跨界隔离工单", "planned": "待排班工单",
        }, day_audit(split)),
        _table("batch_exclusions", "跨界配漆批次隔离明细", ORDER_LABELS, excluded_orders),
        _table("model_effects", "模型系数与训练条件范围", {
            "feature": "工艺条件", "log_coefficient": "标准化对数系数", "mean_multiplier": "每增一个训练标准差的均值倍数",
            "training_sd": "训练标准差", "training_min": "训练最小值", "training_max": "训练最大值",
        }, coefficients),
    ]
    warnings = []
    invalid_count = len(source_issues)
    disputed_count = sum(row.get("measurement_status") == "disputed" for row in split.all_history)
    if invalid_count:
        warnings.append(f"{invalid_count} 条干燥实测无效或存在分歧，已保留导入原值和原因，且不参与拟合或评分。")
    if disputed_count:
        warnings.append(f"{disputed_count} 条工单的补正数值存在分歧，当前暂缓使用；全部补正记录均已留存。")
    if split.excluded:
        batch_count = len({row["prep_batch"] for row in split.excluded})
        warnings.append(f"{batch_count} 个配漆批次跨过切点，较早一侧的 {len(split.excluded)} 条工单已隔离。")
    if out_of_range:
        warnings.append(f"{out_of_range} 条待排班工单超出训练中至少一项工艺范围，发放排班前请核对测读条件。")
    if not split.planned:
        warnings.append("本次来源没有待排班工单，排班对照表为空。")
    if any(fitted.scaler.var_ == 0):
        warnings.append("训练期有工艺条件保持恒定，本次数据无法估计该条件变化带来的影响。")

    return {
        "summary": (
            f"用 {len(split.train)} 条较早完工工单拟合对数链接 Gamma 回归。"
            f"{split.holdout[0]['work_date']} 至 {split.holdout[-1]['work_date']} 的 {len(split.holdout)} 条留出工单"
            f"平均绝对误差为 {scores[0].mae:.2f} 小时。{len(planning)} 条待排班工单中有 {over_plan} 条估时超过预留。"
        ),
        "metrics": {
            "训练工单数": len(split.train),
            "留出工单数": len(split.holdout),
            "留出平均绝对误差（小时）": round(scores[0].mae, 3),
            "留出平均 Gamma 偏差": round(scores[0].gamma_deviance, 6),
            "超预留计划数": over_plan,
        },
        "training_sample_ids": [row["id"] for row in split.train],
        "holdout_sample_ids": [row["id"] for row in split.holdout],
        "training_samples": [row["id"] for row in split.train],
        "holdout_samples": [row["id"] for row in split.holdout],
        "train_samples": [row["id"] for row in split.train],
        "training_sample_count": len(split.train),
        "holdout_sample_count": len(split.holdout),
        "training_rows": [dict(row) for row in split.train],
        "train_rows": [dict(row) for row in split.train],
        "training_records": [dict(row) for row in split.train],
        "issues": source_issues,
        "measurement_issues": source_issues,
        "invalid_measurements": source_issues,
        "holdout_rows": residuals,
        "holdout_records": residuals,
        "training_assigned_sample_ids": [row["id"] for row in split.train_all],
        "holdout_assigned_sample_ids": [row["id"] for row in split.holdout_all],
        "excluded_sample_ids": [row["id"] for row in split.excluded],
        "invalid_sample_ids": [row["id"] for row in split.invalid],
        "planned_sample_ids": [row["id"] for row in split.planned],
        "sample_ids": {
            "training": [row["id"] for row in split.train],
            "holdout": [row["id"] for row in split.holdout],
            "training_assigned": [row["id"] for row in split.train_all],
            "holdout_assigned": [row["id"] for row in split.holdout_all],
            "excluded": [row["id"] for row in split.excluded],
            "invalid": [row["id"] for row in split.invalid],
            "planned": [row["id"] for row in split.planned],
        },
        "sample_assignments": assignment_rows,
        "assignments": assignment_rows,
        "tables": tables,
        "warnings": warnings,
    }
