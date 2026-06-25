"""Typed benchmark contracts for reproducible OHLCV-only backtests."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cogalpha.alpha_contract import DEFAULT_OHLCV_COLUMNS


class ProvenanceStatus(StrEnum):
    """How strongly one benchmark field is backed by a source."""

    PAPER_STATED = "paper_stated"
    CONFIG_STATED = "config_stated"
    INFERRED = "inferred"
    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"


class BenchmarkMetricCategory(StrEnum):
    """Metric families that benchmark reports may include."""

    FACTOR_SCREENING = "factor_screening"
    PORTFOLIO_BACKTEST = "portfolio_backtest"
    RISK = "risk"
    ARTIFACT = "artifact"


class BenchmarkSplitName(StrEnum):
    """Chronological split names used by benchmark specs."""

    TRAIN = "train"
    VALID = "valid"
    TEST = "test"


class SourceReference(BaseModel):
    """Human-auditable source metadata for benchmark fields."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    url: str | None = None
    observed_on: date | None = None
    notes: str | None = None


class ProvenancedValue(BaseModel):
    """A serializable value plus the source confidence behind it."""

    model_config = ConfigDict(extra="forbid")

    value: Any = None
    status: ProvenanceStatus
    source: str | None = None
    notes: str | None = None


class DateRange(BaseModel):
    """Inclusive date range."""

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def _validate_order(self) -> DateRange:
        if self.start > self.end:
            raise ValueError("DateRange.start must be on or before end")
        return self


class SplitWindows(BaseModel):
    """Train/validation/test windows."""

    model_config = ConfigDict(extra="forbid")

    train: DateRange
    valid: DateRange
    test: DateRange

    @model_validator(mode="after")
    def _validate_chronological_order(self) -> SplitWindows:
        if self.train.end >= self.valid.start:
            raise ValueError("train split must end before valid split starts")
        if self.valid.end >= self.test.start:
            raise ValueError("valid split must end before test split starts")
        return self


class BenchmarkUniverse(BaseModel):
    """Target universe contract for a benchmark preset."""

    model_config = ConfigDict(extra="forbid")

    market: ProvenancedValue
    asset_type: ProvenancedValue = Field(default_factory=lambda: _unknown_value("asset_type"))
    target_rule: ProvenancedValue
    target_asset_count: ProvenancedValue | None = None
    benchmark_symbol: ProvenancedValue | None = None
    data_frequency: ProvenancedValue


class LabelDefinition(BaseModel):
    """Label timing and expression contract."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    horizon_days: int = Field(..., ge=0)
    price_column: str = Field(..., min_length=1)
    entry_delay_days: int = Field(default=0, ge=0)
    exit_delay_days: int | None = Field(default=None, ge=1)
    expression: ProvenancedValue | None = None
    timing_notes: ProvenancedValue

    @model_validator(mode="after")
    def _validate_price_column(self) -> LabelDefinition:
        if self.price_column not in DEFAULT_OHLCV_COLUMNS:
            raise ValueError(f"Unsupported OHLCV price column: {self.price_column}")
        return self


class ExecutionRule(BaseModel):
    """Execution price and timing contract."""

    model_config = ConfigDict(extra="forbid")

    deal_price: ProvenancedValue
    signal_to_trade_delay_days: ProvenancedValue
    notes: str | None = None


class TopKDropoutRule(BaseModel):
    """Top-k dropout portfolio selection contract."""

    model_config = ConfigDict(extra="forbid")

    strategy_name: ProvenancedValue
    topk: ProvenancedValue
    n_drop: ProvenancedValue
    rebalance_frequency: ProvenancedValue | None = None


class CostModel(BaseModel):
    """Transaction cost model contract."""

    model_config = ConfigDict(extra="forbid")

    open_cost: ProvenancedValue
    close_cost: ProvenancedValue
    min_cost: ProvenancedValue
    currency: ProvenancedValue


class MetricDefinition(BaseModel):
    """One metric expected in benchmark reports."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    category: BenchmarkMetricCategory
    definition: str = Field(..., min_length=1)
    unit: str | None = None
    source: str | None = None


class ArtifactRequirements(BaseModel):
    """Reproducibility fields benchmark artifacts must contain."""

    model_config = ConfigDict(extra="forbid")

    required_fields: list[str] = Field(default_factory=list)
    output_formats: list[str] = Field(default_factory=lambda: ["json", "markdown"])
    include_resolved_spec: bool = True
    include_source_fingerprints: bool = True
    include_validation_report: bool = True


class BenchmarkSpec(BaseModel):
    """Complete benchmark contract for one named paper/config setting."""

    model_config = ConfigDict(extra="forbid")

    preset_id: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    universe: BenchmarkUniverse
    data_window: DateRange
    splits: SplitWindows
    label: LabelDefinition
    execution: ExecutionRule
    portfolio_rule: TopKDropoutRule
    cost_model: CostModel
    benchmark: ProvenancedValue
    reported_metrics: list[MetricDefinition]
    artifact_requirements: ArtifactRequirements
    sources: list[SourceReference]


def _unknown_value(name: str) -> ProvenancedValue:
    return ProvenancedValue(
        value=None,
        status=ProvenanceStatus.UNKNOWN,
        notes=f"{name} is not confirmed in the current benchmark source.",
    )
