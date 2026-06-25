"""Engine-side benchmark contracts and presets.

After the CLEAN-03 vertical split (21-04), this package holds ONLY the engine-side
``BenchmarkSpec`` surface (``specs.py``) and the presets (``presets.py``) that the engine
consumes via ``cogalpha.protocol``. The governance half -- metric parity, artifact
manifests, spec diff, benchmark data validation, and the scaled-protocol builder -- was
quarantined into ``evidence.benchmark_governance`` (off the engine import path). The
backtest math was re-homed to ``cogalpha.backtest`` (21-01). No governance / backtest
re-export shim is left here (G2).
"""

from cogalpha.benchmark.presets import (
    BENCHMARK_PRESETS,
    COGALPHA_CSI300_OHLCV_V1,
    COGALPHA_PRESET_ID,
    QUANTAALPHA_CSI300_OHLCV_V1,
    QUANTAALPHA_PRESET_ID,
    get_benchmark_spec,
    list_benchmark_presets,
)
from cogalpha.benchmark.specs import (
    ArtifactRequirements,
    BenchmarkMetricCategory,
    BenchmarkSpec,
    BenchmarkSplitName,
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

__all__ = [
    "ArtifactRequirements",
    "BENCHMARK_PRESETS",
    "BenchmarkMetricCategory",
    "BenchmarkSpec",
    "BenchmarkSplitName",
    "BenchmarkUniverse",
    "COGALPHA_CSI300_OHLCV_V1",
    "COGALPHA_PRESET_ID",
    "CostModel",
    "DateRange",
    "ExecutionRule",
    "LabelDefinition",
    "MetricDefinition",
    "ProvenancedValue",
    "ProvenanceStatus",
    "QUANTAALPHA_CSI300_OHLCV_V1",
    "QUANTAALPHA_PRESET_ID",
    "SourceReference",
    "SplitWindows",
    "TopKDropoutRule",
    "get_benchmark_spec",
    "list_benchmark_presets",
]
