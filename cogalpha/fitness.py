"""Fitness metric computation and paper-defined selection gates."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from sklearn.feature_selection import mutual_info_regression

from cogalpha.config import FitnessGateConfig
from cogalpha.schemas import CandidateStage, FitnessDecision, FitnessMetrics

METRIC_FIELDS = ("ic", "rank_ic", "icir", "rank_icir", "mi")
MI_RANDOM_STATE = 0
MI_ESTIMATOR = "mutual_info_regression"


class PredictiveMetricDiagnostics(BaseModel):
    """Source-backed diagnostics for paper/Qlib factor-screening metrics."""

    model_config = ConfigDict(extra="forbid")

    metrics: FitnessMetrics
    split_name: str | None = None
    daily_ic: dict[str, float] = Field(default_factory=dict)
    daily_rank_ic: dict[str, float] = Field(default_factory=dict)
    finite_pair_counts_by_date: dict[str, int] = Field(default_factory=dict)
    mi_sample_count: int = 0
    mi_estimator: str = MI_ESTIMATOR
    mi_random_state: int = MI_RANDOM_STATE
    icir_std_ddof: int = 0
    rank_icir_std_ddof: int = 0
    icir_annualized: bool = False
    rank_icir_annualized: bool = False


def compute_predictive_metrics(
    factor_values: pd.DataFrame | pd.Series,
    forward_returns: pd.DataFrame | pd.Series,
) -> FitnessMetrics:
    """Compute IC, RankIC, ICIR, RankICIR, and MI from aligned panel values."""

    return compute_predictive_metric_diagnostics(
        factor_values,
        forward_returns,
    ).metrics


def compute_predictive_metric_diagnostics(
    factor_values: pd.DataFrame | pd.Series,
    forward_returns: pd.DataFrame | pd.Series,
    *,
    split_name: str | None = None,
) -> PredictiveMetricDiagnostics:
    """Compute scalar factor metrics plus daily series and convention metadata."""

    factors = _as_panel(factor_values)
    returns = _as_panel(forward_returns)
    factors, returns = factors.align(returns, join="inner", axis=None)

    daily_ic: dict[str, float] = {}
    daily_rank_ic: dict[str, float] = {}
    finite_counts: dict[str, int] = {}
    for timestamp in factors.index:
        x = factors.loc[timestamp].to_numpy(dtype=float)
        y = returns.loc[timestamp].to_numpy(dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        date_label = _date_label(timestamp)
        finite_counts[date_label] = int(mask.sum())
        daily_ic[date_label] = _clean_metric_value(_safe_corr(x[mask], y[mask]))
        daily_rank_ic[date_label] = _clean_metric_value(
            _safe_corr(_rank(x[mask]), _rank(y[mask]))
        )

    ic_values = np.asarray(
        [value for value in daily_ic.values() if np.isfinite(value)],
        dtype=float,
    )
    rank_values = np.asarray(
        [value for value in daily_rank_ic.values() if np.isfinite(value)],
        dtype=float,
    )
    mi, mi_sample_count = _mutual_information_with_sample_count(factors, returns)

    return PredictiveMetricDiagnostics(
        metrics=FitnessMetrics(
            ic=_safe_mean(ic_values),
            rank_ic=_safe_mean(rank_values),
            icir=_safe_ir(ic_values),
            rank_icir=_safe_ir(rank_values),
            mi=mi,
        ),
        split_name=split_name,
        daily_ic=daily_ic,
        daily_rank_ic=daily_rank_ic,
        finite_pair_counts_by_date=finite_counts,
        mi_sample_count=mi_sample_count,
    )


def apply_fitness_gate(
    candidate_metrics: Mapping[str, FitnessMetrics],
    config: FitnessGateConfig,
) -> list[FitnessDecision]:
    """Classify candidates using same-generation percentiles and paper minima."""

    if not candidate_metrics:
        return []

    qualified_thresholds = _thresholds(
        candidate_metrics,
        config.qualified_percentile,
        config.qualified_minima,
    )
    elite_thresholds = _thresholds(
        candidate_metrics,
        config.elite_percentile,
        config.elite_minima,
    )

    decisions: list[FitnessDecision] = []
    for candidate_id, metrics in candidate_metrics.items():
        if _passes(metrics, elite_thresholds):
            stage = CandidateStage.ELITE
        elif _passes(metrics, qualified_thresholds):
            stage = CandidateStage.QUALIFIED
        else:
            stage = CandidateStage.REJECTED_BY_FITNESS
        decisions.append(
            FitnessDecision(
                candidate_id=candidate_id,
                metrics=metrics,
                stage=stage,
                qualified_thresholds=qualified_thresholds,
                elite_thresholds=elite_thresholds,
            )
        )
    return decisions


def composite_fitness_score(metrics: FitnessMetrics | None) -> float:
    """Small deterministic score used only for ordering pools and samples."""

    if metrics is None:
        return float("-inf")
    return float(sum(getattr(metrics, field) for field in METRIC_FIELDS))


def _as_panel(values: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(values, pd.DataFrame):
        return values.sort_index().sort_index(axis=1)
    if not isinstance(values.index, pd.MultiIndex) or values.index.nlevels != 2:
        raise ValueError("Series inputs must use a two-level MultiIndex: date, ticker.")
    return values.unstack(level=-1).sort_index().sort_index(axis=1)


def _safe_corr(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 2 or y.size < 2:
        return float("nan")
    if np.nanstd(x) == 0 or np.nanstd(y) == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def _rank(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    return pd.Series(values).rank(method="average").to_numpy(dtype=float)


def _safe_mean(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    return float(np.nanmean(values))


def _safe_ir(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    std = float(np.nanstd(values))
    if std == 0:
        return 0.0
    return float(np.nanmean(values) / std)


def _mutual_information(factors: pd.DataFrame, returns: pd.DataFrame) -> float:
    value, _ = _mutual_information_with_sample_count(factors, returns)
    return value


def _mutual_information_with_sample_count(
    factors: pd.DataFrame,
    returns: pd.DataFrame,
) -> tuple[float, int]:
    x = factors.to_numpy(dtype=float).ravel()
    y = returns.to_numpy(dtype=float).ravel()
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    sample_count = int(x.size)
    if x.size < 4 or np.unique(x).size < 2 or np.unique(y).size < 2:
        return 0.0, sample_count
    try:
        values = mutual_info_regression(x.reshape(-1, 1), y, random_state=MI_RANDOM_STATE)
    except ValueError:
        return 0.0, sample_count
    return float(values[0]), sample_count


def _thresholds(
    candidate_metrics: Mapping[str, FitnessMetrics],
    percentile: float,
    minima: FitnessMetrics,
) -> FitnessMetrics:
    values_by_field = {
        field: [getattr(metrics, field) for metrics in candidate_metrics.values()]
        for field in METRIC_FIELDS
    }
    return FitnessMetrics(
        **{
            field: max(float(np.quantile(values, percentile)), getattr(minima, field))
            for field, values in values_by_field.items()
        }
    )


def _passes(metrics: FitnessMetrics, thresholds: FitnessMetrics) -> bool:
    return all(getattr(metrics, field) >= getattr(thresholds, field) for field in METRIC_FIELDS)


def _date_label(value: object) -> str:
    return pd.Timestamp(value).date().isoformat()


def _clean_metric_value(value: float) -> float:
    if not np.isfinite(value):
        return float(value)
    return float(np.round(value, 12))
