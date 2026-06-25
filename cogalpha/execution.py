"""Restricted execution for generated alpha functions over OHLCV panels."""

from __future__ import annotations

import importlib
import math
from collections.abc import Callable

import numpy as np
import pandas as pd
from scipy import stats

from cogalpha.alpha_contract import (
    ALLOWED_IMPORT_MODULES,
    RUNTIME_OHLCV_COLUMNS,
    RUNTIME_PANEL_INDEX_NAMES,
)
from cogalpha.schemas import AlphaCandidate, AlphaFunction

try:  # pragma: no cover - availability depends on the local TA-Lib install
    import talib
except ModuleNotFoundError:  # pragma: no cover
    talib = None

SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "pow": pow,
    "range": range,
    "round": round,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


class AlphaExecutionError(RuntimeError):
    """Raised when an Alpha Function cannot be executed under the runtime contract."""


def execute_alpha_candidate(
    candidate: AlphaCandidate,
    ohlcv_panel: pd.DataFrame,
) -> pd.Series:
    """Execute one AlphaCandidate against a two-level `(date, ticker)` OHLCV panel."""

    return execute_alpha_function(candidate.alpha, ohlcv_panel)


def execute_alpha_function(
    alpha: AlphaFunction,
    ohlcv_panel: pd.DataFrame,
) -> pd.Series:
    """Return factor values as a `pd.Series` indexed `(date, ticker)` (O-2 / D-05).

    The factor is dispatched per ticker via pandas' grouped C-level dispatch
    (``groupby(level="ticker").apply``) -- there is NO Python ``for`` loop over
    tickers on the hot path. Each single-ticker frame is run through the compiled
    function under the restricted runtime namespace; the per-ticker results are
    reassembled into a single `pd.Series` indexed `(date, ticker)` named as the
    function. The literal explicit per-ticker apply form is kept only as the
    test-time correctness oracle (``tests/test_executor_equivalence.py``).
    """

    function = compile_alpha_function(alpha)
    sorted_panel = _validate_runtime_ohlcv_panel(ohlcv_panel)
    date_level, ticker_level = RUNTIME_PANEL_INDEX_NAMES

    if sorted_panel.index.get_level_values(ticker_level).nunique() == 0:
        raise AlphaExecutionError("OHLCV panel contains no tickers.")

    def _run_group(frame: pd.DataFrame) -> pd.Series:
        ticker_frame = frame.droplevel(ticker_level).loc[:, list(RUNTIME_OHLCV_COLUMNS)]
        # Per-ticker invariants (Series output, index match, name) enforced here,
        # inside pandas' grouped dispatch -- not a Python ticker loop on the hot path.
        return _execute_one_ticker(function, alpha.name, ticker_frame)

    # pandas grouped (C-level) dispatch yields a wide ticker x date frame; stack it
    # back to the long (date, ticker) Series the prompt contract requires. The
    # literal explicit per-ticker apply survives only as the test-time oracle.
    wide = sorted_panel.groupby(level=ticker_level, sort=False, group_keys=True).apply(
        _run_group
    )
    long_values = wide.stack(future_stack=True)
    long_values = (
        long_values.reorder_levels([date_level, ticker_level]).sort_index().rename(alpha.name)
    )
    long_values.index = long_values.index.set_names(list(RUNTIME_PANEL_INDEX_NAMES))
    return long_values


def compile_alpha_function(alpha: AlphaFunction) -> Callable[[pd.DataFrame], pd.Series]:
    """Compile one generated Alpha Function in a restricted namespace."""

    namespace = _runtime_namespace()
    try:
        exec(compile(alpha.code, f"<{alpha.name}>", "exec"), namespace, namespace)
    except Exception as exc:  # noqa: BLE001 - preserve runtime failure as guard context
        raise AlphaExecutionError(f"Failed to compile alpha code: {exc}") from exc

    function = namespace.get(alpha.name)
    if not callable(function):
        raise AlphaExecutionError(f"Alpha code did not define callable {alpha.name!r}.")
    return function


def _validate_runtime_ohlcv_panel(ohlcv_panel: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(ohlcv_panel, pd.DataFrame):
        raise AlphaExecutionError("OHLCV panel must be a pandas DataFrame.")
    if not isinstance(ohlcv_panel.index, pd.MultiIndex) or ohlcv_panel.index.nlevels != 2:
        raise AlphaExecutionError("OHLCV panel must use a two-level MultiIndex: date, ticker.")
    if tuple(ohlcv_panel.index.names) != RUNTIME_PANEL_INDEX_NAMES:
        raise AlphaExecutionError(
            "OHLCV panel MultiIndex names must be exactly date, ticker; "
            f"got {list(ohlcv_panel.index.names)!r}."
        )
    if ohlcv_panel.index.has_duplicates:
        raise AlphaExecutionError("OHLCV panel contains duplicate (date, ticker) rows.")

    missing = [column for column in RUNTIME_OHLCV_COLUMNS if column not in ohlcv_panel.columns]
    if missing:
        raise AlphaExecutionError(
            "OHLCV panel must contain the standard OHLCV columns "
            f"{list(RUNTIME_OHLCV_COLUMNS)!r}; missing {missing!r}."
        )

    unsupported = [column for column in ohlcv_panel.columns if column not in RUNTIME_OHLCV_COLUMNS]
    if unsupported:
        raise AlphaExecutionError(
            "OHLCV panel contains unsupported columns: "
            f"{unsupported!r}. Runtime execution accepts only standard OHLCV columns "
            f"{list(RUNTIME_OHLCV_COLUMNS)!r}."
        )

    return ohlcv_panel.loc[:, list(RUNTIME_OHLCV_COLUMNS)].sort_index()


def _execute_one_ticker(
    function: Callable[[pd.DataFrame], pd.Series],
    function_name: str,
    frame: pd.DataFrame,
) -> pd.Series:
    try:
        output = function(frame.copy())
    except Exception as exc:  # noqa: BLE001 - preserve runtime failure as guard context
        raise AlphaExecutionError(f"Alpha function raised during execution: {exc}") from exc

    if not isinstance(output, pd.Series):
        raise AlphaExecutionError(f"Alpha function {function_name!r} must return a pandas Series.")
    if not output.index.equals(frame.index):
        raise AlphaExecutionError(
            f"Alpha function {function_name!r} output index must match "
            "the input ticker frame index."
        )
    if output.name != function_name:
        raise AlphaExecutionError(
            f"Alpha function {function_name!r} output Series name must match the function name."
        )
    return output


def _runtime_namespace() -> dict[str, object]:
    builtins = dict(SAFE_BUILTINS)
    builtins["__import__"] = _restricted_import
    return {
        "__builtins__": builtins,
        "math": math,
        "np": np,
        "pd": pd,
        "stats": stats,
        "talib": talib,
    }


def _restricted_import(
    name: str,
    globals=None,  # noqa: ANN001 - matches Python's __import__ protocol
    locals=None,  # noqa: ANN001 - matches Python's __import__ protocol
    fromlist=(),  # noqa: ANN001 - matches Python's __import__ protocol
    level: int = 0,
):
    if level != 0:
        raise ImportError("Relative imports are not allowed in Alpha Functions.")
    if name == "scipy" and set(fromlist).issubset({"stats"}):
        return importlib.import_module(name)
    if name not in ALLOWED_IMPORT_MODULES:
        raise ImportError(f"Import {name!r} is outside the alpha runtime allowlist.")
    if name == "talib" and talib is None:
        raise ImportError("TA-Lib is not available in this runtime.")
    return importlib.import_module(name)
