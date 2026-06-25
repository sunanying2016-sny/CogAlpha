"""Engine-path OHLCV-only portfolio construction and backtest utilities.

This is the engine-side home (``cogalpha.backtest``) of the top-50/drop-5
portfolio backtest math, re-homed verbatim from ``cogalpha.benchmark.backtest``
so the production run (the orchestrator ``finalize`` seam) and the deterministic
test suite share ONE source for the construction / trades+costs / return-series /
portfolio-metrics math (BACK-01). The math is full-universe-capable by
construction: ``topk`` / ``n_drop`` are sourced only from the
:class:`~cogalpha.benchmark.specs.BenchmarkSpec`, never a literal universe
ceiling (D-1 anti-cheat / G1).

This module is intentionally free of any Qlib runtime dependency — the AER / IR
formulas are implemented in numpy/pandas. The information ratio keeps the Qlib annualization
convention ``mean / std * sqrt(periods_per_year)`` (= sqrt(252) at daily
frequency); see ``_information_ratio`` (BACK-03 / A4 / Pitfall 4). This
convention is verified-faithful to Qlib and must NOT be switched to a per-window
``sqrt(N)`` normalization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from cogalpha.data import validate_ohlcv_panel

if TYPE_CHECKING:
    # Engine->engine type-only edge (specs.py stays engine-side per Finding C).
    # Imported under TYPE_CHECKING to break the import cycle introduced by
    # re-homing this module to the package root: ``cogalpha.benchmark`` re-exports
    # the backtest symbols, so a runtime ``from cogalpha.benchmark.specs import``
    # here would trigger the partially-initialized ``cogalpha.benchmark`` barrel.
    # ``BenchmarkSpec`` is used only in annotations (``from __future__ import
    # annotations`` makes them lazy strings), never constructed/isinstance-checked.
    from cogalpha.benchmark.specs import BenchmarkSpec


class SignalAlignmentReport(BaseModel):
    """Diagnostics from aligning dated signals to a normalized OHLCV panel."""

    model_config = ConfigDict(extra="forbid")

    input_signal_dates: list[str] = Field(default_factory=list)
    aligned_dates: list[str] = Field(default_factory=list)
    input_assets: list[str] = Field(default_factory=list)
    aligned_assets: list[str] = Field(default_factory=list)
    dropped_signal_dates: list[str] = Field(default_factory=list)
    dropped_assets: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)


class TransactionCostConfig(BaseModel):
    """Resolved transaction cost settings from a benchmark spec."""

    model_config = ConfigDict(extra="forbid")

    open_cost: float = Field(ge=0.0)
    close_cost: float = Field(ge=0.0)
    min_cost: float = Field(ge=0.0)
    currency: str = Field(min_length=1)


class CostSummary(BaseModel):
    """JSON-safe aggregate cost diagnostics."""

    model_config = ConfigDict(extra="forbid")

    trade_count: int = Field(ge=0)
    total_cost: float
    total_cost_return: float
    mean_turnover: float
    currency: str
    initial_capital: float = Field(gt=0.0)


class PortfolioBacktestMetrics(BaseModel):
    """Persisted scalar portfolio backtest metrics."""

    model_config = ConfigDict(extra="forbid")

    cumulative_return: float
    annualized_return: float
    annualized_excess_return: float
    information_ratio: float
    max_drawdown: float
    mean_turnover: float
    total_transaction_cost_return: float


@dataclass(frozen=True)
class SignalAlignmentResult:
    """Aligned signal values plus the OHLCV rows used for construction."""

    signals: pd.DataFrame
    ohlcv_panel: pd.DataFrame
    alignment_report: SignalAlignmentReport


@dataclass(frozen=True)
class PortfolioConstructionResult:
    """Target holdings and diagnostics from top-k/dropout construction."""

    holdings: pd.DataFrame
    alignment_report: SignalAlignmentReport
    topk: int
    n_drop: int
    rebalance_dates: list[str] = field(default_factory=list)
    selected_assets_by_date: dict[str, list[str]] = field(default_factory=dict)
    finite_signal_counts_by_date: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TradeLedger:
    """Trades, turnover, and costs derived from target holdings."""

    trades: pd.DataFrame
    weight_deltas: pd.DataFrame
    turnover: pd.Series
    daily_costs: pd.Series
    cost_returns: pd.Series
    cost_summary: CostSummary


@dataclass(frozen=True)
class PortfolioReturnSeries:
    """Aligned gross, cost, net, benchmark, and excess return series."""

    gross_returns: pd.Series
    cost_returns: pd.Series
    net_returns: pd.Series
    benchmark_returns: pd.Series
    excess_returns: pd.Series
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PortfolioBacktestResult:
    """End-to-end portfolio backtest result with pandas-heavy tables."""

    construction: PortfolioConstructionResult
    trade_ledger: TradeLedger
    return_series: PortfolioReturnSeries
    metrics: PortfolioBacktestMetrics
    warnings: list[str] = field(default_factory=list)


def align_signals_to_ohlcv(
    signals: pd.DataFrame | pd.Series,
    ohlcv_panel: pd.DataFrame,
) -> SignalAlignmentResult:
    """Normalize dated signals and intersect them with observed OHLCV rows."""

    signal_frame = _normalize_signal_frame(signals)
    panel = validate_ohlcv_panel(ohlcv_panel)

    input_signal_dates = _date_labels(signal_frame.index)
    input_assets = sorted(str(asset) for asset in signal_frame.columns)
    panel_dates = pd.Index(panel.index.get_level_values("date").unique()).sort_values()
    panel_assets = pd.Index(
        panel.index.get_level_values("ticker").astype(str).unique()
    ).sort_values()

    candidate_dates = signal_frame.index.intersection(panel_dates).sort_values()
    candidate_assets = pd.Index(input_assets).intersection(panel_assets).sort_values()
    if candidate_dates.empty or candidate_assets.empty:
        raise ValueError("No overlapping signal and OHLCV observations")

    candidate_signals = signal_frame.loc[candidate_dates, candidate_assets]
    observed_pairs = panel.loc[
        panel.index.get_level_values("date").isin(candidate_dates)
        & panel.index.get_level_values("ticker").isin(candidate_assets)
    ].index
    stacked_index = pd.MultiIndex.from_product(
        [candidate_signals.index, candidate_signals.columns],
        names=["date", "ticker"],
    )
    stacked = pd.Series(candidate_signals.to_numpy().reshape(-1), index=stacked_index)
    stacked = stacked.loc[stacked.index.intersection(observed_pairs)]
    if stacked.empty:
        raise ValueError("No overlapping signal and OHLCV observations")

    aligned_signals = stacked.unstack("ticker").sort_index().sort_index(axis=1)
    aligned_panel = panel.loc[observed_pairs].sort_index()
    aligned_dates = _date_labels(aligned_signals.index)
    aligned_assets = sorted(str(asset) for asset in aligned_signals.columns)

    dropped_signal_dates = sorted(set(input_signal_dates) - set(aligned_dates))
    dropped_assets = sorted(set(input_assets) - set(aligned_assets))
    findings: list[str] = []
    if dropped_signal_dates:
        findings.append("dropped_signal_dates")
    if dropped_assets:
        findings.append("dropped_assets")

    return SignalAlignmentResult(
        signals=aligned_signals,
        ohlcv_panel=aligned_panel,
        alignment_report=SignalAlignmentReport(
            input_signal_dates=input_signal_dates,
            aligned_dates=aligned_dates,
            input_assets=input_assets,
            aligned_assets=aligned_assets,
            dropped_signal_dates=dropped_signal_dates,
            dropped_assets=dropped_assets,
            findings=findings,
        ),
    )


def construct_topk_dropout_portfolio(
    signals: pd.DataFrame | pd.Series,
    ohlcv_panel: pd.DataFrame,
    spec: BenchmarkSpec,
) -> PortfolioConstructionResult:
    """Construct deterministic equal-weight top-k/dropout target holdings."""

    alignment = align_signals_to_ohlcv(signals, ohlcv_panel)
    signal_frame = alignment.signals
    # No literal universe ceiling: topk / n_drop are sourced only from the spec
    # and ``target_count`` is min(topk, available finite assets) — never a
    # hardcoded ``[:N]`` asset cap (D-1 anti-cheat; full-universe-capable).
    topk = int(spec.portfolio_rule.topk.value)
    n_drop = int(spec.portfolio_rule.n_drop.value)
    holdings = pd.DataFrame(0.0, index=signal_frame.index, columns=signal_frame.columns)
    selected_by_date: dict[str, list[str]] = {}
    finite_counts: dict[str, int] = {}
    warnings: list[str] = []
    previous_selected: list[str] = []

    for date_value, row in signal_frame.iterrows():
        date_label = _date_label(date_value)
        finite_row = row[pd.Series(np.isfinite(row.to_numpy(dtype=float)), index=row.index)]
        ranked_assets = sorted(
            [str(asset) for asset in finite_row.index],
            key=lambda asset: (-float(finite_row.loc[asset]), asset),
        )
        finite_counts[date_label] = len(ranked_assets)
        if len(ranked_assets) < topk:
            warning = f"{date_label}:fewer_finite_assets_than_topk"
            warnings.append(warning)

        target_count = min(topk, len(ranked_assets))
        if target_count == 0:
            selected: list[str] = []
        elif not previous_selected:
            selected = ranked_assets[:target_count]
        else:
            rank = {asset: position for position, asset in enumerate(ranked_assets)}
            retained = [asset for asset in previous_selected if asset in rank]
            if n_drop > 0 and retained:
                drop_count = min(n_drop, len(retained))
                worst_retained = sorted(
                    retained,
                    key=lambda asset: (rank[asset], asset),
                    reverse=True,
                )
                dropped = set(worst_retained[:drop_count])
                retained = [asset for asset in retained if asset not in dropped]
            selected = retained[:target_count]
            for asset in ranked_assets:
                if len(selected) >= target_count:
                    break
                if asset not in selected:
                    selected.append(asset)

        if selected:
            holdings.loc[date_value, selected] = 1.0 / len(selected)
        selected_by_date[date_label] = selected
        previous_selected = selected

    return PortfolioConstructionResult(
        holdings=holdings,
        alignment_report=alignment.alignment_report,
        topk=topk,
        n_drop=n_drop,
        rebalance_dates=_date_labels(signal_frame.index),
        selected_assets_by_date=selected_by_date,
        finite_signal_counts_by_date=finite_counts,
        warnings=warnings,
    )


def construction_summary(result: PortfolioConstructionResult) -> dict:
    """Return JSON-safe construction diagnostics for artifact writers."""

    return {
        "topk": result.topk,
        "n_drop": result.n_drop,
        "aligned_date_count": len(result.alignment_report.aligned_dates),
        "aligned_asset_count": len(result.alignment_report.aligned_assets),
        "rebalance_count": len(result.rebalance_dates),
    }


def cost_config_from_spec(spec: BenchmarkSpec) -> TransactionCostConfig:
    """Resolve transaction cost settings from a benchmark spec."""

    return TransactionCostConfig(
        open_cost=float(spec.cost_model.open_cost.value),
        close_cost=float(spec.cost_model.close_cost.value),
        min_cost=float(spec.cost_model.min_cost.value),
        currency=str(spec.cost_model.currency.value),
    )


def compute_trades_and_costs(
    holdings: pd.DataFrame,
    spec: BenchmarkSpec,
    initial_capital: float = 1_000_000.0,
) -> TradeLedger:
    """Convert target holdings into trades, turnover, and transaction-cost drag."""

    if initial_capital <= 0.0:
        raise ValueError("initial_capital must be positive.")
    config = cost_config_from_spec(spec)
    target = _normalize_holdings(holdings)
    previous = target.shift(1, fill_value=0.0)
    deltas = target - previous
    trade_rows: list[dict] = []
    for date_value, row in deltas.iterrows():
        for asset, weight_delta in row.items():
            delta = float(weight_delta)
            if delta == 0.0:
                continue
            side = "buy" if delta > 0.0 else "sell"
            rate = config.open_cost if side == "buy" else config.close_cost
            notional = abs(delta) * initial_capital
            fee = max(notional * rate, config.min_cost)
            trade_rows.append(
                {
                    "date": pd.Timestamp(date_value),
                    "asset": str(asset),
                    "side": side,
                    "weight_delta": delta,
                    "notional": notional,
                    "rate": rate,
                    "fee": fee,
                }
            )

    trades = pd.DataFrame(
        trade_rows,
        columns=["date", "asset", "side", "weight_delta", "notional", "rate", "fee"],
    )
    if not trades.empty:
        trades = trades.sort_values(["date", "asset", "side"]).reset_index(drop=True)
        daily_costs = trades.groupby("date")["fee"].sum().reindex(target.index, fill_value=0.0)
    else:
        daily_costs = pd.Series(0.0, index=target.index, name="daily_cost")
    turnover = (deltas.abs().sum(axis=1) / 2.0).rename("turnover")
    daily_costs = daily_costs.astype(float).rename("daily_cost")
    cost_returns = (daily_costs / float(initial_capital)).rename("cost_return")

    return TradeLedger(
        trades=trades,
        weight_deltas=deltas,
        turnover=turnover,
        daily_costs=daily_costs,
        cost_returns=cost_returns,
        cost_summary=CostSummary(
            trade_count=len(trades),
            total_cost=float(daily_costs.sum()),
            total_cost_return=float(cost_returns.sum()),
            mean_turnover=float(turnover.mean()) if len(turnover) else 0.0,
            currency=config.currency,
            initial_capital=float(initial_capital),
        ),
    )


def compute_execution_price_returns(
    ohlcv_panel: pd.DataFrame,
    spec: BenchmarkSpec,
) -> pd.DataFrame:
    """Compute next execution-period returns from the spec execution price column."""

    panel = validate_ohlcv_panel(ohlcv_panel)
    price_column = str(spec.execution.deal_price.value)
    if price_column not in panel.columns:
        raise ValueError(f"Execution price column not found in OHLCV panel: {price_column}")
    prices = panel[price_column].unstack("ticker").sort_index().sort_index(axis=1)
    return (prices.shift(-1) / prices - 1.0).sort_index().sort_index(axis=1)


def compute_portfolio_return_series(
    holdings: pd.DataFrame,
    ohlcv_panel: pd.DataFrame,
    spec: BenchmarkSpec,
    cost_drag: pd.Series,
    benchmark_returns: pd.Series | None = None,
    missing_benchmark_policy: str = "record_missing",
) -> PortfolioReturnSeries:
    """Compute timing-safe portfolio, benchmark, and excess return series."""

    target = _normalize_holdings(holdings)
    asset_returns = compute_execution_price_returns(ohlcv_panel, spec)
    asset_returns = asset_returns.reindex(index=target.index, columns=target.columns)
    gross_returns = (target * asset_returns.fillna(0.0)).sum(axis=1).rename("gross_return")
    cost_returns = _normalize_series(cost_drag, target.index, "cost_return").fillna(0.0)
    net_returns = (gross_returns - cost_returns).rename("net_return")

    warnings: list[str] = []
    if benchmark_returns is None:
        warnings.append("benchmark_returns_missing")
        if missing_benchmark_policy == "zero_fill":
            benchmark = pd.Series(0.0, index=target.index, name="benchmark_return")
            excess = net_returns.rename("excess_return")
        elif missing_benchmark_policy == "record_missing":
            benchmark = pd.Series(np.nan, index=target.index, name="benchmark_return")
            excess = pd.Series(np.nan, index=target.index, name="excess_return")
        else:
            raise ValueError(
                "missing_benchmark_policy must be 'record_missing' or 'zero_fill'."
            )
    else:
        benchmark = _normalize_series(
            benchmark_returns,
            target.index,
            "benchmark_return",
        )
        excess = (net_returns - benchmark).rename("excess_return")

    return PortfolioReturnSeries(
        gross_returns=gross_returns,
        cost_returns=cost_returns,
        net_returns=net_returns,
        benchmark_returns=benchmark,
        excess_returns=excess,
        warnings=warnings,
    )


def compute_portfolio_metrics(
    return_series: PortfolioReturnSeries,
    turnover: pd.Series,
    periods_per_year: int = 252,
) -> PortfolioBacktestMetrics:
    """Compute JSON-safe scalar portfolio metrics."""

    net = pd.to_numeric(return_series.net_returns, errors="coerce").fillna(0.0)
    excess = pd.to_numeric(return_series.excess_returns, errors="coerce").dropna()
    cost_returns = pd.to_numeric(return_series.cost_returns, errors="coerce").fillna(0.0)
    turnover_values = pd.to_numeric(turnover, errors="coerce").fillna(0.0)

    cumulative_return = float((1.0 + net).prod() - 1.0) if len(net) else 0.0
    annualized_return = _annualize_compounded(cumulative_return, len(net), periods_per_year)
    annualized_excess_return = _annualize_series(excess, periods_per_year)
    information_ratio = _information_ratio(excess, periods_per_year)
    if len(net):
        wealth = (1.0 + net).cumprod()
        drawdown = wealth / wealth.cummax() - 1.0
        max_drawdown = float(drawdown.min())
    else:
        max_drawdown = 0.0

    return PortfolioBacktestMetrics(
        cumulative_return=cumulative_return,
        annualized_return=annualized_return,
        annualized_excess_return=annualized_excess_return,
        information_ratio=information_ratio,
        max_drawdown=max_drawdown,
        mean_turnover=float(turnover_values.mean()) if len(turnover_values) else 0.0,
        total_transaction_cost_return=float(cost_returns.sum()),
    )


def run_portfolio_backtest(
    signals: pd.DataFrame | pd.Series,
    ohlcv_panel: pd.DataFrame,
    spec: BenchmarkSpec,
    benchmark_returns: pd.Series | None = None,
    initial_capital: float = 1_000_000.0,
    periods_per_year: int = 252,
) -> PortfolioBacktestResult:
    """Run construction, costs, returns, and metrics for one OHLCV-only backtest."""

    construction = construct_topk_dropout_portfolio(signals, ohlcv_panel, spec)
    trade_ledger = compute_trades_and_costs(
        construction.holdings,
        spec,
        initial_capital=initial_capital,
    )
    return_series = compute_portfolio_return_series(
        construction.holdings,
        ohlcv_panel,
        spec,
        trade_ledger.cost_returns,
        benchmark_returns=benchmark_returns,
    )
    metrics = compute_portfolio_metrics(
        return_series,
        trade_ledger.turnover,
        periods_per_year=periods_per_year,
    )
    return PortfolioBacktestResult(
        construction=construction,
        trade_ledger=trade_ledger,
        return_series=return_series,
        metrics=metrics,
        warnings=[*construction.warnings, *return_series.warnings],
    )


def _normalize_signal_frame(signals: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(signals, pd.Series):
        if not isinstance(signals.index, pd.MultiIndex) or signals.index.nlevels != 2:
            raise ValueError("Signal Series must use a two-level MultiIndex: date, ticker.")
        frame = signals.unstack(level=-1)
    elif isinstance(signals, pd.DataFrame):
        if isinstance(signals.index, pd.MultiIndex) and signals.index.nlevels == 2:
            if signals.shape[1] != 1:
                preferred = [
                    name for name in ("signal", "score", "value") if name in signals.columns
                ]
                if not preferred:
                    raise ValueError(
                        "MultiIndex signal DataFrame must have one column or a signal column."
                    )
                series = signals[preferred[0]]
            else:
                series = signals.iloc[:, 0]
            frame = series.unstack(level=-1)
        else:
            frame = signals.copy()
    else:
        raise ValueError("Signals must be a pandas DataFrame or Series.")

    if isinstance(frame.index, pd.MultiIndex):
        raise ValueError("Wide signal DataFrame index must be date-like, not MultiIndex.")

    normalized = frame.copy()
    dates = pd.to_datetime(normalized.index, errors="coerce")
    if dates.isna().any():
        raise ValueError("Signals contain invalid dates.")
    normalized.index = pd.Index(dates, name="date")
    normalized.columns = [str(asset) for asset in normalized.columns]
    normalized = normalized.apply(pd.to_numeric, errors="coerce")
    normalized = normalized.sort_index().sort_index(axis=1)
    if normalized.index.has_duplicates:
        normalized = normalized.groupby(level=0).last()
    return normalized


def _normalize_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(holdings, pd.DataFrame):
        raise ValueError("Holdings must be a pandas DataFrame.")
    normalized = holdings.copy()
    normalized.index = pd.Index(pd.to_datetime(normalized.index, errors="coerce"), name="date")
    if normalized.index.isna().any():
        raise ValueError("Holdings contain invalid dates.")
    normalized.columns = [str(asset) for asset in normalized.columns]
    normalized = normalized.apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return normalized.sort_index().sort_index(axis=1)


def _normalize_series(values: pd.Series, index: pd.Index, name: str) -> pd.Series:
    series = values.copy()
    series.index = pd.Index(pd.to_datetime(series.index, errors="coerce"), name="date")
    if series.index.isna().any():
        raise ValueError(f"{name} contains invalid dates.")
    series = pd.to_numeric(series, errors="coerce")
    return series.reindex(index).rename(name)


def _annualize_compounded(cumulative_return: float, periods: int, periods_per_year: int) -> float:
    if periods <= 0:
        return 0.0
    if cumulative_return <= -1.0:
        return -1.0
    return float((1.0 + cumulative_return) ** (periods_per_year / periods) - 1.0)


def _annualize_series(values: pd.Series, periods_per_year: int) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return 0.0
    cumulative = float((1.0 + clean).prod() - 1.0)
    return _annualize_compounded(cumulative, len(clean), periods_per_year)


def _information_ratio(values: pd.Series, periods_per_year: int) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return 0.0
    std = float(np.nanstd(clean.to_numpy(dtype=float)))
    if std == 0.0:
        return 0.0
    mean = float(np.nanmean(clean.to_numpy(dtype=float)))
    # BACK-03 / A4 / Pitfall 4: sqrt(periods_per_year) is the verified-faithful
    # Qlib IR annualization (= sqrt(252) at daily frequency). Do NOT switch this
    # to a per-window sqrt(N) normalization — it would diverge from Qlib's IR.
    return float(mean / std * np.sqrt(periods_per_year))


def _date_labels(values: pd.Index) -> list[str]:
    return [_date_label(value) for value in values]


def _date_label(value: object) -> str:
    return pd.Timestamp(value).date().isoformat()
