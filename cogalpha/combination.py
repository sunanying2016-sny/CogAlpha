"""Config-selectable single-model combination trainer (BACK-02).

This is the ONE genuinely greenfield Phase-21 engine component. It turns the
elite / ``final_candidate_pool`` factor *values* + the 10-day forward-return
label into ONE combined signal — a ``(date,ticker)`` two-level-MultiIndex
``pd.Series`` — via a rolling-126 train/predict loop with a strict
no-look-ahead embargo.

Two independent methods, never combined
----------------------------------------
The paper (App B.4 / §4.6 / Table 5, sha256-verified ``46ef590b…2181c6``) treats
Ridge and LightGBM as two **independent** training methods, reported as separate
rows (``CogAlpha Ridge`` / ``CogAlpha LightGBM``). The trainer selects EXACTLY ONE
of them by config (``method ∈ {ridge, lightgbm}``, default ``lightgbm`` to match
the paper headline CSI300-10d numbers). There is no averaging, weighting,
residual model, or combined meta model anywhere in this module. The earlier
internal "fusion" framing (D-2) is superseded by Finding A.

Feature-matrix contract (A5)
----------------------------
``features`` is the elite / ``final_candidate_pool`` factor values stacked as
columns over the ``(date,ticker)`` panel — one column per elite factor. ``label``
is the 10-day forward return on the same ``(date,ticker)`` index. The output is
one combined signal value per predicted ``(date,ticker)`` pair, in the same
two-level-MultiIndex ``pd.Series`` shape the backtest's
``_normalize_signal_frame`` consumes. The ``(date,ticker)`` alignment reuses
``cogalpha.fitness._as_panel`` (O-2) rather than a second hand-rolled alignment.

Paper-pinned system variables (PROTO-04)
----------------------------------------
``rolling_step`` (=126) and ``label_horizon`` (=10) and the App B.4 hyper-params
are paper-system variables, NOT free run knobs: ``CombinationConfig`` rejects any
drift under a strict ``extra="forbid"`` + ``@model_validator`` (mirroring
``protocol.py``'s ``_PINNED_*`` discipline). The exact Ridge/LGBM hyper-params
live in :func:`make_combination_model`, sourced verbatim from App B.4.

Hyper-params left free (library defaults)
-----------------------------------------
Params NOT named by App B.4 stay at library defaults and are deliberately left
unset: LightGBM ``min_child_samples`` / ``objective``; Ridge ``solver``. For
LightGBM the trainer additionally fixes ``random_state`` and ``n_jobs=1`` so a
synthetic-panel run is reproducible in structure (Ridge is byte-stable;
LightGBM floats are not asserted — Pitfall 6).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from pydantic import BaseModel, ConfigDict, model_validator
from sklearn.base import RegressorMixin
from sklearn.linear_model import Ridge

from cogalpha.fitness import _as_panel

# --- Paper-pinned system variables (PROTO-04). Drift is rejected by the config
# validator: these are NOT free run knobs (App B.4 / §4.6).
_PINNED_ROLLING_STEP = 126
_PINNED_LABEL_HORIZON = 10

# --- Determinism controls for the LightGBM path (Pitfall 6). Not paper-pinned
# numerics — only reproducibility plumbing so the synthetic-panel structural
# test is stable. Ridge needs neither.
_LIGHTGBM_RANDOM_STATE = 0
_LIGHTGBM_N_JOBS = 1


def make_combination_model(method: str) -> RegressorMixin:
    """Return the single App B.4 regressor selected by ``method`` (NO fusion).

    ``"ridge"`` -> ``Ridge(alpha=10.0)``; ``"lightgbm"`` -> ``LGBMRegressor`` with
    the verbatim App B.4 hyper-params. Any other value raises ``ValueError``
    (no silent fallback, no second model). Exactly one regressor per call.

    The ``subsample`` / ``colsample_bytree`` kwargs are the sklearn-API aliases
    for the paper's ``bagging_fraction`` / ``feature_fraction``; ``n_estimators``
    is the number of trees. LightGBM gets a fixed ``random_state`` / ``n_jobs=1``
    for reproducible structure.
    """

    if method == "ridge":
        return Ridge(alpha=10.0)
    if method == "lightgbm":
        return LGBMRegressor(
            learning_rate=0.0001,
            num_leaves=32,
            max_depth=12,
            reg_alpha=1.0,
            reg_lambda=1.0,
            n_estimators=1000,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=_LIGHTGBM_RANDOM_STATE,
            n_jobs=_LIGHTGBM_N_JOBS,
            verbose=-1,
        )
    raise ValueError(f"unknown combination method: {method!r}")


class CombinationConfig(BaseModel):
    """Strict config for the combination trainer (PROTO-04 paper-pinned).

    ``method`` selects the single regressor (default ``"lightgbm"`` for the
    paper headline numbers). ``rolling_step`` and ``label_horizon`` are
    paper-system variables pinned to 126 / 10 by the validator — any drift
    raises, mirroring ``protocol.py``'s ``_PINNED_*`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["ridge", "lightgbm"] = "lightgbm"
    rolling_step: int = _PINNED_ROLLING_STEP
    label_horizon: int = _PINNED_LABEL_HORIZON

    @model_validator(mode="after")
    def _enforce_paper_pins(self) -> CombinationConfig:
        """Pin the paper-system variables (PROTO-04 'scale data, not system')."""

        if self.rolling_step != _PINNED_ROLLING_STEP:
            raise ValueError(
                "rolling_step is paper-pinned to 126 (App B.4 / PROTO-04); "
                "it is not a free run knob"
            )
        if self.label_horizon != _PINNED_LABEL_HORIZON:
            raise ValueError(
                "label_horizon is paper-pinned to 10 (CSI300 10-day label / "
                "PROTO-04); it is not a free run knob"
            )
        return self


def train_combination_signal(
    features: pd.DataFrame | pd.Series,
    label: pd.Series,
    config: CombinationConfig,
    *,
    _force_embargo_violation: bool = False,
) -> pd.Series:
    """Rolling-126 combination trainer -> ONE combined ``(date,ticker)`` signal.

    Walk the time axis in ``config.rolling_step`` (126)-day steps. At each step,
    fit ``make_combination_model(config.method)`` on the training window strictly
    BEFORE the prediction block, then predict the next 126-day block. The predict
    blocks tile the prediction span with no gap and no overlap; the returned
    combined-signal index equals the union of predicted ``(date,ticker)`` pairs
    exactly (long two-level-MultiIndex ``pd.Series``).

    No-look-ahead embargo (fail-closed): because the label is a
    ``config.label_horizon`` (=10) day forward return, the training window's
    labels must end at least 10 trading days before the first predicted feature
    date. ``max(train_label_date) + label_horizon <= min(predict_feature_date)``
    is asserted at each rolling step and raises on violation (extending the
    Phase-19 temporal-leakage discipline to the combination layer).

    One config-driven code path (D-1): tests pass a small synthetic panel (and a
    LightGBM config) THROUGH this same function; the production config is the App
    B.4 one — there is no separate reduced-scale branch.

    ``_force_embargo_violation`` is a test-only hook that artificially places the
    last training-window label date inside the embargo gap so the fail-closed
    assertion can be exercised; it is never set on the production path.
    """

    # Reuse the O-2 (date,ticker) alignment. For a feature DataFrame this keeps
    # the (date,ticker) MultiIndex with one column per elite factor; the label
    # Series is unstacked to a date x ticker frame purely to recover its date
    # axis, then re-stacked per block.
    feature_panel = _as_panel(features)
    label_frame = _as_panel(label)

    factor_columns = list(_feature_columns(features))
    date_axis = label_frame.index  # single-level, sorted by _as_panel
    n_dates = len(date_axis)
    step = config.rolling_step
    horizon = config.label_horizon

    if n_dates <= step:
        raise ValueError(
            "feature panel must span more than rolling_step days to predict any "
            f"block (got {n_dates} days, rolling_step={step})"
        )

    predicted_frames: list[pd.Series] = []

    # The first `step` days seed the initial training window; prediction begins at
    # the first day on/after `step` and tiles forward in `step`-day blocks with no
    # gap and no overlap.
    for block_start in range(step, n_dates, step):
        predict_dates = date_axis[block_start : block_start + step]
        if len(predict_dates) == 0:
            break
        min_predict_feature_date = predict_dates[0]

        # --- No-look-ahead embargo: the label is a `horizon`-day forward return,
        # so the training window must end a `horizon`-day gap before the first
        # predicted feature date. Carve that gap out of the training tail.
        train_end_position = block_start - horizon
        if _force_embargo_violation:
            # Test-only: keep training right up to the predict block (no gap) so
            # the fail-closed inequality below trips.
            train_end_position = block_start
        train_dates = date_axis[:train_end_position]
        if len(train_dates) == 0:
            continue
        max_train_label_date = train_dates[-1]

        # Fail-closed assertion: max(train_label_date) + horizon <= min(predict).
        if train_end_position + horizon > block_start:
            raise ValueError(
                "no-look-ahead embargo violated: max(train_label_date) + "
                f"label_horizon ({max_train_label_date.date()} + {horizon}) must "
                f"be <= min(predict_feature_date) ({min_predict_feature_date.date()})"
            )

        # --- Supervised design over the train window. Drop rows with any missing
        # feature or label so the regressor sees a clean matrix.
        x_train = feature_panel.loc[_date_mask(feature_panel, train_dates), factor_columns]
        y_train = label.loc[_date_mask(label, train_dates)]
        train_design = pd.concat(
            [x_train, y_train.rename("__label__")], axis=1
        ).dropna()
        if train_design.empty:
            continue

        model = make_combination_model(config.method)
        model.fit(
            train_design[factor_columns].to_numpy(),
            train_design["__label__"].to_numpy(),
        )

        # --- Predict the block. Keep only rows with complete features.
        x_predict = feature_panel.loc[
            _date_mask(feature_panel, predict_dates), factor_columns
        ].dropna()
        if x_predict.empty:
            continue
        predictions = model.predict(x_predict.to_numpy())
        predicted_frames.append(
            pd.Series(predictions, index=x_predict.index, name="combined_signal")
        )

    if not predicted_frames:
        return pd.Series(
            [],
            index=pd.MultiIndex.from_arrays([[], []], names=["date", "ticker"]),
            name="combined_signal",
            dtype=float,
        )

    signal = pd.concat(predicted_frames).sort_index()
    signal.index = signal.index.set_names(["date", "ticker"])
    return signal


def _feature_columns(features: pd.DataFrame | pd.Series) -> pd.Index:
    """Return the elite-factor column labels of the feature panel."""

    if isinstance(features, pd.Series):
        return pd.Index([features.name if features.name is not None else 0])
    return features.columns


def _date_mask(obj: pd.DataFrame | pd.Series, dates: pd.Index) -> np.ndarray:
    """Boolean mask selecting rows of ``obj`` whose ``date`` level is in ``dates``."""

    return obj.index.get_level_values("date").isin(dates)
