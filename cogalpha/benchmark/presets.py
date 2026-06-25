"""Named benchmark presets for CogAlpha and QuantaAlpha settings."""

from __future__ import annotations

from datetime import date

from cogalpha.benchmark.specs import (
    ArtifactRequirements,
    BenchmarkMetricCategory,
    BenchmarkSpec,
    BenchmarkUniverse,
    CostModel,
    DateRange,
    ExecutionRule,
    LabelDefinition,
    MetricDefinition,
    ProvenancedValue,
    ProvenanceStatus,
    SourceReference,
    SplitWindows,
    TopKDropoutRule,
)

COGALPHA_PRESET_ID = "cogalpha_csi300_ohlcv_v1"
QUANTAALPHA_PRESET_ID = "quantaalpha_csi300_ohlcv_v1"

_COGALPHA_SOURCE = "CogAlpha project paper notes and BaselineExperimentConfig"
_QUANTAALPHA_SOURCE = "QuantaAlpha public configs/backtest.yaml"


def _paper(value, *, source: str = _COGALPHA_SOURCE, notes: str | None = None) -> ProvenancedValue:
    return ProvenancedValue(
        value=value,
        status=ProvenanceStatus.PAPER_STATED,
        source=source,
        notes=notes,
    )


def _config(
    value,
    *,
    source: str = _QUANTAALPHA_SOURCE,
    notes: str | None = None,
) -> ProvenancedValue:
    return ProvenancedValue(
        value=value,
        status=ProvenanceStatus.CONFIG_STATED,
        source=source,
        notes=notes,
    )


def _inferred(value, *, source: str, notes: str) -> ProvenancedValue:
    return ProvenancedValue(
        value=value,
        status=ProvenanceStatus.INFERRED,
        source=source,
        notes=notes,
    )


def _metric(name: str, category: BenchmarkMetricCategory, definition: str) -> MetricDefinition:
    return MetricDefinition(name=name, category=category, definition=definition)


def _artifact_requirements() -> ArtifactRequirements:
    return ArtifactRequirements(
        required_fields=[
            "resolved_spec",
            "preset_id",
            "data_version",
            "universe",
            "split_windows",
            "label_definition",
            "portfolio_rule",
            "cost_model",
            "benchmark",
            "metric_definitions",
            "source_fingerprints",
        ]
    )


COGALPHA_CSI300_OHLCV_V1 = BenchmarkSpec(
    preset_id=COGALPHA_PRESET_ID,
    version="v1",
    title="CogAlpha CSI300 OHLCV benchmark",
    description=(
        "CogAlpha-first CSI300 OHLCV paper setting for reproducible benchmark "
        "contracts before portfolio backtesting."
    ),
    universe=BenchmarkUniverse(
        market=_paper("CSI300"),
        asset_type=_inferred(
            "A-share equity",
            source=_COGALPHA_SOURCE,
            notes="Derived from the CSI300 benchmark context.",
        ),
        target_rule=_paper("CSI300 large-cap A-share constituents"),
        target_asset_count=_paper(300, notes="Paper setting describes roughly 300 constituents."),
        benchmark_symbol=_paper("SH000300"),
        data_frequency=_paper("daily"),
    ),
    data_window=DateRange(start=date(2011, 1, 1), end=date(2024, 12, 1)),
    splits=SplitWindows(
        train=DateRange(start=date(2011, 1, 1), end=date(2019, 12, 31)),
        valid=DateRange(start=date(2020, 1, 1), end=date(2020, 12, 31)),
        test=DateRange(start=date(2021, 1, 1), end=date(2024, 12, 1)),
    ),
    label=LabelDefinition(
        name="10-day next-open forward return",
        horizon_days=10,
        price_column="open",
        entry_delay_days=1,
        expression=_paper("forward open return over 10 trading observations"),
        timing_notes=_paper(
            "Signal observes date t OHLCV, enters at the next open, and exits after "
            "10 trading opens."
        ),
    ),
    execution=ExecutionRule(
        deal_price=_paper("open"),
        signal_to_trade_delay_days=_paper(1),
    ),
    portfolio_rule=TopKDropoutRule(
        strategy_name=_paper("TopkDropoutStrategy"),
        topk=_paper(50),
        n_drop=_paper(5),
        rebalance_frequency=_inferred(
            "daily",
            source=_COGALPHA_SOURCE,
            notes="Recorded as daily because the benchmark consumes daily OHLCV bars.",
        ),
    ),
    cost_model=CostModel(
        open_cost=_paper(0.0005),
        close_cost=_paper(0.0015),
        min_cost=_paper(5),
        currency=_paper("CNY"),
    ),
    benchmark=_paper("SH000300"),
    reported_metrics=[
        _metric("IC", BenchmarkMetricCategory.FACTOR_SCREENING, "Information coefficient."),
        _metric("RankIC", BenchmarkMetricCategory.FACTOR_SCREENING, "Rank correlation IC."),
        _metric("ICIR", BenchmarkMetricCategory.FACTOR_SCREENING, "IC information ratio."),
        _metric(
            "annualized_return",
            BenchmarkMetricCategory.PORTFOLIO_BACKTEST,
            "Annualized portfolio return; implemented in a later phase.",
        ),
        _metric(
            "max_drawdown",
            BenchmarkMetricCategory.RISK,
            "Maximum drawdown; implemented in a later phase.",
        ),
    ],
    artifact_requirements=_artifact_requirements(),
    sources=[
        SourceReference(
            name=_COGALPHA_SOURCE,
            notes="Local planning docs and config preserve the first CogAlpha reproduction target.",
        )
    ],
)


QUANTAALPHA_CSI300_OHLCV_V1 = BenchmarkSpec(
    preset_id=QUANTAALPHA_PRESET_ID,
    version="v1",
    title="QuantaAlpha CSI300 OHLCV comparison benchmark",
    description=(
        "QuantaAlpha public backtest configuration captured as a comparison reference, "
        "not an equal-depth CogAlpha reproduction target for Phase 1."
    ),
    universe=BenchmarkUniverse(
        market=_config("csi300"),
        asset_type=_inferred(
            "A-share equity",
            source=_QUANTAALPHA_SOURCE,
            notes="Derived from the CSI300 market identifier.",
        ),
        target_rule=_config("csi300"),
        target_asset_count=_inferred(
            300,
            source=_QUANTAALPHA_SOURCE,
            notes="CSI300 implies an approximately 300-asset target universe.",
        ),
        benchmark_symbol=_config("SH000300"),
        data_frequency=_config("daily"),
    ),
    data_window=DateRange(start=date(2016, 1, 1), end=date(2025, 12, 26)),
    splits=SplitWindows(
        train=DateRange(start=date(2016, 1, 1), end=date(2020, 12, 31)),
        valid=DateRange(start=date(2021, 1, 1), end=date(2021, 12, 31)),
        test=DateRange(start=date(2022, 1, 1), end=date(2025, 12, 26)),
    ),
    label=LabelDefinition(
        name="QuantaAlpha next-day close expression",
        horizon_days=1,
        price_column="close",
        entry_delay_days=1,
        expression=_config("Ref($close, -2) / Ref($close, -1) - 1"),
        timing_notes=_config(
            "Expression is recorded from public config and not evaluated by Phase 1."
        ),
    ),
    execution=ExecutionRule(
        deal_price=_config("open"),
        signal_to_trade_delay_days=_config(1),
    ),
    portfolio_rule=TopKDropoutRule(
        strategy_name=_config("TopkDropoutStrategy"),
        topk=_config(50),
        n_drop=_config(5),
        rebalance_frequency=_inferred(
            "daily",
            source=_QUANTAALPHA_SOURCE,
            notes="Recorded from daily backtest/data settings.",
        ),
    ),
    cost_model=CostModel(
        open_cost=_config(0.0005),
        close_cost=_config(0.0015),
        min_cost=_config(5),
        currency=_inferred(
            "CNY",
            source=_QUANTAALPHA_SOURCE,
            notes="Inferred from China A-share benchmark context.",
        ),
    ),
    benchmark=_config("SH000300"),
    reported_metrics=[
        _metric(
            "annualized_return",
            BenchmarkMetricCategory.PORTFOLIO_BACKTEST,
            "Annualized portfolio return reported by the public backtest config.",
        ),
        _metric(
            "information_ratio",
            BenchmarkMetricCategory.PORTFOLIO_BACKTEST,
            "Benchmark-relative information ratio.",
        ),
        _metric("max_drawdown", BenchmarkMetricCategory.RISK, "Maximum drawdown."),
    ],
    artifact_requirements=_artifact_requirements(),
    sources=[
        SourceReference(
            name=_QUANTAALPHA_SOURCE,
            url="https://github.com/QuantaAlpha/QuantaAlpha",
            observed_on=date(2026, 6, 6),
            notes="Captured for comparison-reference settings only.",
        )
    ],
)


BENCHMARK_PRESETS: dict[str, BenchmarkSpec] = {
    COGALPHA_CSI300_OHLCV_V1.preset_id: COGALPHA_CSI300_OHLCV_V1,
    QUANTAALPHA_CSI300_OHLCV_V1.preset_id: QUANTAALPHA_CSI300_OHLCV_V1,
}


def list_benchmark_presets() -> list[str]:
    """Return available benchmark preset ids in deterministic order."""

    return sorted(BENCHMARK_PRESETS)


def get_benchmark_spec(preset_id: str) -> BenchmarkSpec:
    """Return a deep-copy resolved benchmark spec for a preset id."""

    try:
        spec = BENCHMARK_PRESETS[preset_id]
    except KeyError:
        raise ValueError(f"Unknown benchmark preset: {preset_id}") from None
    return spec.model_copy(deep=True)
