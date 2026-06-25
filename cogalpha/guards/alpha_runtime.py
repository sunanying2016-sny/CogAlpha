"""Runtime numerical guard for generated alpha functions."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from cogalpha.execution import AlphaExecutionError, execute_alpha_candidate
from cogalpha.schemas import AlphaCandidate, GuardIssue, GuardReport, GuardStatus


def run_runtime_alpha_code_guard(
    candidate: AlphaCandidate,
    ohlcv_panel: pd.DataFrame,
    max_nan_fraction: float = 0.30,
    timeout_seconds: float | None = None,
) -> GuardReport:
    """Execute an AlphaCandidate and reject unstable numerical outputs.

    The ``timeout_seconds`` parameter is retained for call-signature
    compatibility (``stages/quality.py``, ``guards/pipeline.py``) but no longer
    drives any timeout mechanism: per-candidate timeout is enforced upstream by
    ``cogalpha.execution_pool.AlphaExecPool`` (CONC-01, the single timeout
    authority). The previous main-thread-only interval-timer signal path was
    removed because it silently no-ops under concurrency.
    """

    started = time.perf_counter()
    try:
        # Executor returns a (date, ticker) Series (O-2 / D-05); the numerical
        # stability checks operate on the wide date x ticker frame.
        factor_series = execute_alpha_candidate(candidate, ohlcv_panel)
    except AlphaExecutionError as exc:
        return build_runtime_execution_failure_report(
            candidate_id=candidate.candidate_id,
            message=str(exc),
            max_nan_fraction=max_nan_fraction,
            started=started,
        )

    return build_runtime_numeric_stability_report(
        candidate_id=candidate.candidate_id,
        factor_series=factor_series,
        max_nan_fraction=max_nan_fraction,
        started=started,
    )


def build_runtime_execution_failure_report(
    *,
    candidate_id: str,
    message: str,
    max_nan_fraction: float = 0.30,
    started: float | None = None,
) -> GuardReport:
    """Return a FAIL runtime guard report for an execution failure (fail-closed).

    Used both for an in-process ``AlphaExecutionError`` and for the pool-routed
    fail-closed disposition (D-01a): a candidate whose untrusted code could not be
    executed in the isolated pool is dispositioned an execution failure here, never
    silently re-run in-process.
    """

    metadata = _base_metadata(candidate_id=candidate_id, max_nan_fraction=max_nan_fraction)
    metadata["elapsed_seconds"] = _elapsed_seconds(started) if started is not None else 0.0
    return GuardReport(
        guard_name="runtime_alpha_code",
        status=GuardStatus.FAIL,
        issues=[
            GuardIssue(
                code="runtime_error",
                message=message,
                location=candidate_id,
            )
        ],
        metadata=metadata,
    )


def build_runtime_numeric_stability_report(
    *,
    candidate_id: str,
    factor_series: pd.Series,
    max_nan_fraction: float = 0.30,
    started: float | None = None,
) -> GuardReport:
    """Run the NaN/inf/non-numeric stability checks over an executed factor series.

    The factor series is the ``(date, ticker)`` output the pool (or the in-process
    executor) produced; this function owns ONLY the numerical-stability disposition
    (it never executes untrusted code). Shared by ``run_runtime_alpha_code_guard``
    and the pool-routed fitness/quality paths so the disposition stays identical.
    """

    if started is None:
        started = time.perf_counter()
    metadata = _base_metadata(candidate_id=candidate_id, max_nan_fraction=max_nan_fraction)
    factor_values = factor_series.unstack("ticker")

    issues: list[GuardIssue] = []
    numeric_values = factor_values.apply(lambda series: pd.to_numeric(series, errors="coerce"))
    non_numeric_mask = factor_values.notna() & numeric_values.isna()
    raw_values = numeric_values.to_numpy(dtype=float)
    total_count = raw_values.size
    non_numeric_count = int(non_numeric_mask.to_numpy(dtype=bool).sum())

    if total_count == 0:
        issues.append(
            GuardIssue(
                code="empty_output",
                message="Alpha execution produced an empty factor panel.",
                location=candidate_id,
            )
        )
        nan_fraction = 1.0
        nan_count = 0
        inf_count = 0
    else:
        nan_count = int(np.isnan(raw_values).sum())
        inf_count = int(np.isinf(raw_values).sum())
        nan_fraction = nan_count / total_count
        if non_numeric_count:
            issues.append(
                GuardIssue(
                    code="non_numeric_output",
                    message="Alpha execution produced values that cannot be converted to numeric.",
                    location=candidate_id,
                )
            )
        if nan_count == total_count and not non_numeric_count:
            issues.append(
                GuardIssue(
                    code="all_nan_output",
                    message="Alpha execution produced only NaN values.",
                    location=candidate_id,
                )
            )
        elif nan_fraction > max_nan_fraction:
            issues.append(
                GuardIssue(
                    code="too_many_nan_values",
                    message=(
                        f"Alpha execution produced NaN fraction {nan_fraction:.3f}, "
                        f"above limit {max_nan_fraction:.3f}."
                    ),
                    location=candidate_id,
                )
            )
        if inf_count:
            issues.append(
                GuardIssue(
                    code="non_finite_output",
                    message="Alpha execution produced infinite values.",
                    location=candidate_id,
                )
            )

    status = GuardStatus.FAIL if issues else GuardStatus.PASS
    metadata.update(
        {
            "rows": int(numeric_values.shape[0]),
            "assets": int(numeric_values.shape[1]),
            "total_count": int(total_count),
            "nan_count": int(nan_count),
            "nan_fraction": float(nan_fraction),
            "inf_count": int(inf_count),
            "non_finite_count": int(nan_count + inf_count),
            "non_numeric_count": int(non_numeric_count),
            "elapsed_seconds": _elapsed_seconds(started),
        }
    )
    return GuardReport(
        guard_name="runtime_alpha_code",
        status=status,
        issues=issues,
        metadata=metadata,
    )


def _base_metadata(
    *,
    candidate_id: str,
    max_nan_fraction: float,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "rows": 0,
        "assets": 0,
        "total_count": 0,
        "nan_count": 0,
        "nan_fraction": 0.0,
        "inf_count": 0,
        "non_finite_count": 0,
        "non_numeric_count": 0,
        "max_nan_fraction": float(max_nan_fraction),
        "elapsed_seconds": 0.0,
    }


def _elapsed_seconds(started: float) -> float:
    return round(time.perf_counter() - started, 6)
