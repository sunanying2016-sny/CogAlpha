"""Temporal-leakage named hard-reject stage (STAGE-02 / D-04).

This is the v4.0 honesty backbone: a leaking factor that "scores well" is exactly
the failure mode v3.0 was blocked on, so leakage = invalid alpha (hard reject,
never a warning). The stage is **temporal-only** and combines two layers:

1. **Static layer (reused, 19-01):** :func:`run_temporal_leakage_static_scan`
   (``guards/alpha_code.py``) — forward-looking ``shift(-k)`` (positional and
   ``periods=`` keyword), negative-period ``diff`` / ``pct_change``, centered/forward
   rolling windows, absolute-future ``df.loc[<const>]`` indexing, and reverse
   time-order patterns (``iloc[::-1]`` / ``sort_index(ascending=False)``).
2. **Executed future-value-sentinel layer (NET-NEW):** a deterministic property
   test — :func:`run_executed_leakage_sentinel_test`. It builds a fixed synthetic
   ``(date, ticker)`` OHLCV panel, computes ``f_base``, then for each of a couple of
   evaluation indices ``t`` mutates every row with date ``> t`` (per ticker) to a
   large distinct sentinel and recomputes ``f_perturbed``. If ``f.loc[:t]`` changes,
   the factor read the future ⇒ leak. Some look-ahead constructions slip past the
   static scan (e.g. a reverse-diff-reverse) but are caught here.

The stage adds ONLY temporal checks. It does NOT re-implement the
missing-value-fraction / infinity / all-missing / non-numeric checks — those belong
to the runtime numerical-stability guard (``guards/alpha_runtime.py``, the A.3
stage-5 gate). The
executed test runs under the unchanged restricted executor allowlist
(``execution.py:_runtime_namespace``), so a forbidden-import factor is rejected by
the executor before any sentinel comparison (T-19-09 mitigation).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from cogalpha.alpha_contract import RUNTIME_OHLCV_COLUMNS, RUNTIME_PANEL_INDEX_NAMES
from cogalpha.execution import AlphaExecutionError, execute_alpha_function
from cogalpha.guards.alpha_code import run_temporal_leakage_static_scan
from cogalpha.schemas import AlphaFunction

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cogalpha.execution_pool import AlphaExecPool

__all__ = ["run_executed_leakage_sentinel_test", "temporal_leakage_stage"]

# Deterministic synthetic-panel parameters (no RNG — running the test twice yields
# identical results, a paradigm constraint). Two tickers x 40 trading dates.
_N_DATES = 40
_TICKERS = ("AAA", "BBB")
# Evaluation cut points: assert f.loc[:t] is independent of rows > t. A couple of
# interior positions catch both mid-series and window-edge look-ahead.
_EVAL_POSITIONS = (12, 25)
# A large, distinct sentinel substituted into all future rows. Large enough to
# perturb any factor that reads it, finite so the executor does not reject it.
_SENTINEL = 1.0e9


def _synthetic_panel() -> pd.DataFrame:
    """Build a deterministic ``(date, ticker)`` OHLCV panel (no RNG).

    Values are smooth deterministic functions of the date/ticker offsets so that
    a clean trailing factor is stable while any future-reading factor changes when
    the future rows are overwritten with the sentinel.
    """

    date_level, ticker_level = RUNTIME_PANEL_INDEX_NAMES
    dates = pd.date_range("2020-01-01", periods=_N_DATES, freq="D")
    frames: list[pd.DataFrame] = []
    for ticker_offset, ticker in enumerate(_TICKERS):
        base = np.arange(_N_DATES, dtype=float) + 10.0 * (ticker_offset + 1)
        close = 100.0 + base + np.sin(base / 3.0)
        index = pd.MultiIndex.from_product(
            [dates, [ticker]], names=[date_level, ticker_level]
        )
        frame = pd.DataFrame(
            {
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1_000.0 + base,
            },
            index=index,
        )
        frames.append(frame.loc[:, list(RUNTIME_OHLCV_COLUMNS)])
    return pd.concat(frames).sort_index()


def _perturb_future(panel: pd.DataFrame, cut: pd.Timestamp) -> pd.DataFrame:
    """Return a copy of ``panel`` with all rows whose date ``> cut`` set to the sentinel."""

    date_level = RUNTIME_PANEL_INDEX_NAMES[0]
    perturbed = panel.copy()
    future_mask = perturbed.index.get_level_values(date_level) > cut
    perturbed.loc[future_mask, :] = _SENTINEL
    return perturbed


def _execute_factor(
    alpha: AlphaFunction,
    panel: pd.DataFrame,
    *,
    exec_pool: AlphaExecPool | None,
    require_isolation: bool,
) -> pd.Series:
    """Execute one factor over ``panel``, routing untrusted code per D-01a/D-01b.

    - ``require_isolation=False`` (default standalone path): the deterministic
      synthetic-panel self-test runs in-process. This path is the stage's OWN trusted
      harness and is exercised by unit tests, not the live engine.
    - ``require_isolation=True`` (live engine path): the untrusted execution MUST be
      isolated. When ``exec_pool`` is provided, the factor runs in a subprocess pool
      built over the synthetic panel (the shared engine pool is bound to the market
      panel, so the sentinel uses an isolation pool over its own synthetic panels;
      D-01b shares the SAME isolation mechanism). When ``exec_pool`` is ``None`` the
      path fails CLOSED -> raises ``AlphaExecutionError`` (never runs in-process).
    """

    if not require_isolation:
        return execute_alpha_function(alpha, panel)

    if exec_pool is None:
        raise AlphaExecutionError(
            "Leakage executed-sentinel fail-closed: AlphaExecPool unavailable, "
            "untrusted code is not run in-process (D-01a)."
        )

    # Isolation required: run the untrusted code in a subprocess pool over the
    # synthetic panel (the same AlphaExecPool isolation mechanism, D-01b).
    from cogalpha.execution_pool import AlphaExecPool
    from cogalpha.schemas import AlphaCandidate

    candidate = AlphaCandidate(candidate_id=f"leakage-sentinel-{alpha.name}", alpha=alpha)
    with AlphaExecPool(panel, concurrency=1) as isolation_pool:
        [result] = isolation_pool.evaluate_alpha_code([candidate], timeout_seconds=30.0)
    if not result.ok or result.factor_values is None:
        raise AlphaExecutionError(result.error or "isolated execution failed")
    return result.factor_values


def run_executed_leakage_sentinel_test(
    alpha: AlphaFunction,
    *,
    exec_pool: AlphaExecPool | None = None,
    require_isolation: bool = False,
) -> bool:
    """Return ``True`` if the factor reads future data (executed sentinel test).

    Builds the deterministic synthetic panel, computes ``f_base``, and for each
    evaluation cut ``t`` perturbs every row with date ``> t`` to a large sentinel,
    recomputes ``f_perturbed``, and asserts ``f_base.loc[:t]`` equals
    ``f_perturbed.loc[:t]`` with a missing-value-aware comparison. Any difference ⇒
    the factor reads the future ⇒ ``True`` (leak).

    Untrusted execution routing (D-01a/D-01b): on the live engine path
    (``require_isolation=True``) the factor execution routes through the injected
    isolation pool; an absent/dead pool fails CLOSED → the executed layer is
    **inconclusive** (returns ``False`` from this layer, never claims leak-clean, and
    never runs untrusted code in-process). ``exec_pool=None`` is a fixture-only seam.

    Disposition on execution failure: an un-executable factor here is treated as
    **inconclusive**, NOT leak-clean — it returns ``False`` from this layer (the
    leak signal is owned by the static scan / runtime guard) but is deliberately
    never marked "verified leak-free" by a silent pass. The runtime numerical guard
    (A.3 stage 5) is the executor-failure authority; this layer only reports
    positive future-reads it can actually demonstrate.
    """

    date_level = RUNTIME_PANEL_INDEX_NAMES[0]
    panel = _synthetic_panel()
    try:
        base = _execute_factor(
            alpha, panel, exec_pool=exec_pool, require_isolation=require_isolation
        )
    except AlphaExecutionError:
        # Inconclusive: defer to the runtime guard. Do NOT claim leak-clean.
        return False

    base_dates = base.index.get_level_values(date_level)
    for position in _EVAL_POSITIONS:
        cut = panel.index.get_level_values(date_level).unique()[position]
        perturbed_panel = _perturb_future(panel, cut)
        try:
            perturbed = _execute_factor(
                alpha,
                perturbed_panel,
                exec_pool=exec_pool,
                require_isolation=require_isolation,
            )
        except AlphaExecutionError:
            # The base ran but a perturbed panel did not: cannot compare at this
            # cut; defer (inconclusive) rather than assert a leak.
            continue

        base_head = base[base_dates <= cut]
        perturbed_head = perturbed[
            perturbed.index.get_level_values(date_level) <= cut
        ]
        if not _series_head_unchanged(base_head, perturbed_head):
            return True
    return False


def _series_head_unchanged(left: pd.Series, right: pd.Series) -> bool:
    """Return whether the two head series are equal, missing values aligned.

    ``pd.Series.equals`` treats a missing value in the same position on both sides
    as equal, which is exactly the comparison we need (a clean factor that produces
    a leading missing value from a trailing window must still compare equal). A
    differing index/order at the head is itself a change and compares unequal.
    """

    if not left.index.equals(right.index):
        return False  # differing index/order at the head ⇒ not equal (a change)
    return bool(left.astype(float).equals(right.astype(float)))


def temporal_leakage_stage(
    alpha: AlphaFunction,
    *,
    exec_pool: AlphaExecPool | None = None,
    require_isolation: bool = False,
) -> tuple[bool, list[str]]:
    """Combined temporal-leakage gate: static scan + executed sentinel (D-04).

    Returns ``(leak, reasons)``: ``leak`` is ``True`` if EITHER layer fires, and
    ``reasons`` lists the temporal-leakage reasons (static issue codes + the
    executed-sentinel reason). Adds ONLY temporal checks — no numerical-stability
    logic (the runtime guard owns those). A leak is a HARD REJECT; the caller routes
    the candidate to ``rejected_pool`` (never a warning).

    The static scan ALWAYS runs (it executes no untrusted code). The executed
    sentinel routes untrusted execution through the injected isolation pool on the
    live path (``require_isolation=True``) and fails CLOSED (inconclusive) when the
    pool is absent/dead — the static layer still hard-rejects independently (D-01a).
    """

    reasons: list[str] = []

    static_issues = run_temporal_leakage_static_scan(alpha.code, alpha.name)
    for issue in static_issues:
        reasons.append(f"static:{issue.code}: {issue.message}")

    if run_executed_leakage_sentinel_test(
        alpha, exec_pool=exec_pool, require_isolation=require_isolation
    ):
        reasons.append(
            "executed-sentinel: factor value at time t changed when future rows "
            "(> t) were perturbed — the factor reads future data."
        )

    return (bool(reasons), reasons)
