"""Forward holdout and Gamma GLM using only the retained training records."""

from dataclasses import dataclass, field
from math import ceil, fsum
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import GammaRegressor
from sklearn.preprocessing import StandardScaler

from .records import FEATURES, Row, is_usable_measurement


@dataclass(frozen=True)
class Partition:
    train: list[Row]
    holdout: list[Row]
    excluded: list[Row]
    planned: list[Row]
    #: History rows whose measurement cannot currently be used, excluding rows
    #: already listed as batch-isolated.
    invalid: list[Row] = field(default_factory=list)
    #: Every row on the holdout side of the date cut, including bad measurements.
    holdout_all: list[Row] = field(default_factory=list)
    #: Every non-isolated row on the early side, including bad measurements.
    train_all: list[Row] = field(default_factory=list)
    all_history: list[Row] = field(default_factory=list)
    cutoff: str | None = None
    holdout_day_count: int | None = None

    @property
    def training(self) -> list[Row]:
        return self.train

    @property
    def usable_holdout(self) -> list[Row]:
        return self.holdout

    @property
    def assigned_holdout(self) -> list[Row]:
        return self.holdout_all


def chronological_split(rows: list[Row], holdout_percent: float) -> Partition:
    ordered = sorted(rows, key=lambda row: (row["work_date"], row["id"]))
    history = [row for row in ordered if row["record_type"] == "history"]
    dates = sorted({row["work_date"] for row in history})
    day_count = max(2, ceil(len(dates) * holdout_percent / 100))
    cutoff = dates[-day_count]
    holdout_all = [row for row in history if row["work_date"] >= cutoff]
    early_rows = [row for row in history if row["work_date"] < cutoff]

    # Only complete/usable holdout measurements define leakage isolation.  A bad
    # measurement is retained by date but cannot score; once a consensus
    # correction completes the holdout sample, its prep batch isolates early rows.
    holdout = [row for row in holdout_all if is_usable_measurement(row)]
    holdout_batches = {row["prep_batch"] for row in holdout}

    train = []
    excluded = []
    invalid = []
    train_all = []
    for row in early_rows:
        if row["prep_batch"] in holdout_batches:
            excluded.append(row)
            continue
        train_all.append(row)
        if is_usable_measurement(row):
            train.append(row)
        else:
            invalid.append(row)

    for row in holdout_all:
        if not is_usable_measurement(row):
            invalid.append(row)

    if len(train) < 16 or len({row["work_date"] for row in train}) < 6:
        raise ValueError("配漆批次隔离后，有效训练集须至少保留 16 条工单和 6 个有有效实测的作业日。请补齐较早历史或减少留出比例。")
    if len(holdout) < 4:
        raise ValueError("留出日期须至少覆盖 4 条有效已完工工单。请补正坏实测、补齐末期历史或提高留出比例。")
    return Partition(
        train=train,
        holdout=holdout,
        excluded=excluded,
        planned=[row for row in ordered if row["record_type"] == "planned"],
        invalid=invalid,
        holdout_all=holdout_all,
        train_all=train_all,
        all_history=history,
        cutoff=cutoff,
        holdout_day_count=day_count,
    )


def feature_matrix(rows: list[Row]) -> np.ndarray:
    return np.asarray([[row[key] for key in FEATURES] for row in rows], dtype=float)


@dataclass(frozen=True)
class Fit:
    scaler: StandardScaler
    model: GammaRegressor
    train_mean: float
    feature_min: np.ndarray
    feature_max: np.ndarray

    def predict(self, rows: list[Row]) -> list[float]:
        if not rows:
            return []
        try:
            with np.errstate(over="raise", invalid="raise", under="ignore"):
                predictions = self.model.predict(self.scaler.transform(feature_matrix(rows)))
        except FloatingPointError as error:
            raise ValueError("预测超出计算范围，请检查工艺条件的跨度及训练数据。") from error
        if not np.all(np.isfinite(predictions) & (predictions > 0)):
            raise ValueError("模型未能生成有效的正值工时，请检查工艺条件的跨度及训练数据。")
        return predictions.tolist()


def fit_gamma(train: list[Row], alpha: float) -> Fit:
    usable = [row for row in train if is_usable_measurement(row)]
    if len(usable) < 16:
        raise ValueError("有效训练实测不足 16 条，无法拟合 Gamma 模型。")
    features = feature_matrix(usable)
    targets = [float(row["dry_hours"]) for row in usable]
    scaler = StandardScaler().fit(features)
    model = GammaRegressor(alpha=alpha, solver="lbfgs", max_iter=1_000, tol=1e-9)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(scaler.transform(features), targets)
    except ConvergenceWarning as error:
        raise ValueError("Gamma 拟合未收敛。请增大正则系数，或检查历史工时与工艺记录。") from error
    return Fit(scaler, model, fsum(targets) / len(targets), features.min(axis=0), features.max(axis=0))
