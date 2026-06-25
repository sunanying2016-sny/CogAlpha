"""Direct-Qlib source probes, blockers, and Phase 13 evidence manifests."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cogalpha.data import (
    MarketDataError,
    normalize_ohlcv_panel,
)

QLIB_OHLCV_FIELDS = ["$open", "$high", "$low", "$close", "$volume"]
QLIB_PROBE_FIELDS = [*QLIB_OHLCV_FIELDS, "$factor"]
DIRECT_QLIB_DECISION_IDS = ["D-02", "D-06", "D-07", "D-08", "D-09", "D-12"]
DIRECT_QLIB_FORBIDDEN_CLAIMS = [
    "full_paper_reproduction",
    "paper_qlib_price_parity",
    "qlib_equivalent_backtest_evidence",
    "portfolio_metric_parity",
    "transaction_cost_validation",
]


class _StrictModel(BaseModel):
    """Strict persisted Phase 13 direct-Qlib contract base."""

    model_config = ConfigDict(extra="forbid")


class QlibEnvironmentStatus(_StrictModel):
    """Python and pyqlib importability gate for direct-Qlib execution."""

    python_version: str
    python_version_supported: bool
    pyqlib_importable: bool
    qlib_version: str | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=lambda: DIRECT_QLIB_FORBIDDEN_CLAIMS.copy())
    blocked: bool = False
    decision_ids: list[str] = Field(default_factory=lambda: ["D-02", "D-07", "D-09"])

    @model_validator(mode="after")
    def _derive_blocked(self) -> QlibEnvironmentStatus:
        blocked = bool(self.missing_inputs) or not self.python_version_supported
        object.__setattr__(self, "blocked", blocked)
        return self


class QlibAdjustmentEvidence(_StrictModel):
    """Evidence for Qlib adjustment/factor semantics."""

    adjustment_status: str = Field(..., min_length=1)
    factor_field_observed: bool = False
    original_price_reconstruction_observed: bool = False
    fields_observed: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=lambda: ["D-08"])
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _forbid_price_parity_when_unresolved(self) -> QlibAdjustmentEvidence:
        if self.adjustment_status == "unresolved":
            claims = set(self.forbidden_claims)
            if "paper_qlib_price_parity" not in claims:
                raise ValueError(
                    "unresolved adjustment evidence must forbid paper_qlib_price_parity"
                )
        return self


class QlibCalendarEvidence(_StrictModel):
    """Trading-calendar evidence from Qlib."""

    market: str
    start_date: str | None = None
    end_date: str | None = None
    trading_days: int = Field(default=0, ge=0)
    first_trading_day: str | None = None
    last_trading_day: str | None = None
    missing_inputs: list[str] = Field(default_factory=list)


class QlibBenchmarkReturnEvidence(_StrictModel):
    """SH000300 benchmark-return availability and provenance evidence."""

    benchmark_symbol: str
    available: bool
    rows: int = Field(default=0, ge=0)
    start_date: str | None = None
    end_date: str | None = None
    return_column: str = "open_to_open_return"
    path: str | None = None
    missing_inputs: list[str] = Field(default_factory=list)


class DirectQlibProbeResult(_StrictModel):
    """Direct-Qlib probe result before panel export."""

    data_source: str = "direct_qlib"
    provider_uri: str | None = None
    market: str
    benchmark_symbol: str
    environment: QlibEnvironmentStatus
    calendar: QlibCalendarEvidence
    instruments: list[str] = Field(default_factory=list)
    available_fields: list[str] = Field(default_factory=list)
    adjustment: QlibAdjustmentEvidence
    benchmark_returns: QlibBenchmarkReturnEvidence
    missing_inputs: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=lambda: DIRECT_QLIB_FORBIDDEN_CLAIMS.copy())
    claim_status: str = "backtest_readiness_only"
    decision_ids: list[str] = Field(default_factory=lambda: DIRECT_QLIB_DECISION_IDS.copy())
    notes: list[str] = Field(default_factory=list)


class DirectQlibSourceManifest(_StrictModel):
    """Persisted direct-Qlib source manifest or fail-closed blocker."""

    manifest_id: str = "phase13-direct-qlib-source"
    created_at: str = "not_recorded_for_reproducible_phase13_artifacts"
    data_source: str = "direct_qlib"
    provider_uri: str | None = None
    market: str
    benchmark_symbol: str
    data_version: str
    data_version_payload: dict[str, Any]
    environment: QlibEnvironmentStatus
    calendar: QlibCalendarEvidence | None = None
    instruments: list[str] = Field(default_factory=list)
    available_fields: list[str] = Field(default_factory=list)
    adjustment: QlibAdjustmentEvidence
    benchmark_returns: QlibBenchmarkReturnEvidence
    output_paths: dict[str, str] = Field(default_factory=dict)
    hf_fallback_evidence: dict[str, Any] | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=lambda: DIRECT_QLIB_FORBIDDEN_CLAIMS.copy())
    claim_status: str = Field(..., min_length=1)
    decision_ids: list[str] = Field(default_factory=lambda: DIRECT_QLIB_DECISION_IDS.copy())
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_claim_boundaries(self) -> DirectQlibSourceManifest:
        required_ids = {"D-02", "D-06", "D-07", "D-08", "D-09", "D-12"}
        missing_ids = sorted(required_ids - set(self.decision_ids))
        if missing_ids:
            raise ValueError(f"direct-Qlib manifest missing decision ids: {missing_ids}")

        if self.adjustment.adjustment_status == "unresolved":
            if "paper_qlib_price_parity" not in self.forbidden_claims:
                raise ValueError("unresolved adjustment requires paper_qlib_price_parity blocker")
            if self.claim_status == "backtest_readiness_only":
                raise ValueError("unresolved adjustment cannot be backtest_readiness_only")

        if not self.benchmark_returns.available and (
            "sh000300_benchmark_returns" not in self.missing_inputs
        ):
            raise ValueError("missing SH000300 returns must be recorded in missing_inputs")

        if self.claim_status == "backtest_readiness_only" and self.missing_inputs:
            raise ValueError("backtest_readiness_only requires no missing direct-Qlib inputs")

        if "qlib_equivalent_backtest_evidence" not in self.forbidden_claims:
            raise ValueError("direct-Qlib manifest must forbid Qlib-equivalent backtest claims")

        return self


class DirectQlibExportConfig(_StrictModel):
    """Configuration for direct-Qlib probe/export commands."""

    provider_uri: str | None = None
    market: str = "csi300"
    benchmark_symbol: str = "SH000300"
    output_dir: str = "data/processed/direct_qlib_csi300"
    start_date: str | None = None
    end_date: str | None = None
    frequency: str = "day"
    no_network_download: bool = True


def check_direct_qlib_environment(
    *,
    python_version: tuple[int, int, int] | None = None,
    import_module: Callable[[str], Any] | None = None,
) -> QlibEnvironmentStatus:
    """Return a fail-closed Python/pyqlib environment gate result."""

    resolved_version = python_version or sys.version_info[:3]
    version_text = ".".join(str(part) for part in resolved_version)
    supported = (3, 8, 0) <= resolved_version < (3, 13, 0)
    missing_inputs: list[str] = []
    if not supported:
        missing_inputs.append("python_version_unsupported")
        return QlibEnvironmentStatus(
            python_version=version_text,
            python_version_supported=False,
            pyqlib_importable=False,
            qlib_version=None,
            missing_inputs=missing_inputs,
        )

    importer = import_module or importlib.import_module
    try:
        qlib_module = importer("qlib")
    except ModuleNotFoundError:
        return QlibEnvironmentStatus(
            python_version=version_text,
            python_version_supported=True,
            pyqlib_importable=False,
            qlib_version=None,
            missing_inputs=["pyqlib"],
        )
    except (ImportError, OSError) as exc:
        return QlibEnvironmentStatus(
            python_version=version_text,
            python_version_supported=True,
            pyqlib_importable=False,
            qlib_version=None,
            missing_inputs=[f"pyqlib_import_failed:{type(exc).__name__}"],
        )

    return QlibEnvironmentStatus(
        python_version=version_text,
        python_version_supported=True,
        pyqlib_importable=True,
        qlib_version=str(getattr(qlib_module, "__version__", "unknown")),
        missing_inputs=[],
    )


def probe_direct_qlib_source(
    config: DirectQlibExportConfig,
    *,
    qlib_module: Any | None = None,
) -> DirectQlibProbeResult:
    """Probe Qlib calendar, instruments, fields, adjustment, and benchmark returns."""

    qlib_obj = qlib_module or importlib.import_module("qlib")
    environment = QlibEnvironmentStatus(
        python_version=".".join(str(part) for part in sys.version_info[:3]),
        python_version_supported=True,
        pyqlib_importable=True,
        qlib_version=str(getattr(qlib_obj, "__version__", "unknown")),
        missing_inputs=[],
    )
    _init_qlib(qlib_obj, config)
    data_api = qlib_obj.data.D

    calendar_index = pd.to_datetime(
        data_api.calendar(
            start_time=config.start_date,
            end_time=config.end_date,
            freq=config.frequency,
        )
    )
    calendar = QlibCalendarEvidence(
        market=config.market,
        start_date=config.start_date,
        end_date=config.end_date,
        trading_days=len(calendar_index),
        first_trading_day=_date_text(calendar_index.min()) if len(calendar_index) else None,
        last_trading_day=_date_text(calendar_index.max()) if len(calendar_index) else None,
        missing_inputs=[] if len(calendar_index) else ["qlib_trading_calendar"],
    )

    instruments_obj = data_api.instruments(config.market)
    instruments = [
        str(item).upper()
        for item in data_api.list_instruments(
            instruments_obj,
            start_time=config.start_date,
            end_time=config.end_date,
            as_list=True,
        )
    ]
    feature_frame = data_api.features(
        instruments=instruments,
        fields=QLIB_PROBE_FIELDS,
        start_time=config.start_date,
        end_time=config.end_date,
        freq=config.frequency,
    )
    available_fields = [field for field in QLIB_PROBE_FIELDS if field in feature_frame.columns]
    adjustment = _build_adjustment_evidence(available_fields)
    benchmark_returns = _benchmark_evidence_from_series(
        config.benchmark_symbol,
        load_sh000300_benchmark_returns(config, qlib_module=qlib_obj),
    )

    missing_inputs = []
    if not instruments:
        missing_inputs.append("csi300_instruments")
    if calendar.missing_inputs:
        missing_inputs.extend(calendar.missing_inputs)
    if not benchmark_returns.available:
        missing_inputs.extend(benchmark_returns.missing_inputs)

    claim_status = "backtest_readiness_only"
    if missing_inputs or adjustment.adjustment_status == "unresolved":
        claim_status = "blocked_missing_direct_qlib_evidence"

    return DirectQlibProbeResult(
        provider_uri=config.provider_uri,
        market=config.market,
        benchmark_symbol=config.benchmark_symbol,
        environment=environment,
        calendar=calendar,
        instruments=instruments,
        available_fields=available_fields,
        adjustment=adjustment,
        benchmark_returns=benchmark_returns,
        missing_inputs=_dedupe(missing_inputs),
        claim_status=claim_status,
        notes=[
            "Phase 13 records Qlib backtest-readiness evidence only.",
            (
                "Formal portfolio parity, AER/IR parity, and transaction-cost validation "
                "stay in Phase 14."
            ),
        ],
    )


def extract_qlib_ohlcv_panel(
    config: DirectQlibExportConfig,
    *,
    qlib_module: Any | None = None,
) -> pd.DataFrame:
    """Extract direct-Qlib OHLCV fields into the project panel contract."""

    qlib_obj = qlib_module or importlib.import_module("qlib")
    _init_qlib(qlib_obj, config)
    data_api = qlib_obj.data.D
    instruments = data_api.list_instruments(
        data_api.instruments(config.market),
        start_time=config.start_date,
        end_time=config.end_date,
        as_list=True,
    )
    raw = data_api.features(
        instruments=[str(item).upper() for item in instruments],
        fields=QLIB_OHLCV_FIELDS,
        start_time=config.start_date,
        end_time=config.end_date,
        freq=config.frequency,
    )
    qlib_panel = _qlib_features_to_date_asset(raw)
    return normalize_ohlcv_panel(
        qlib_panel,
        column_map={
            "$open": "open",
            "$high": "high",
            "$low": "low",
            "$close": "close",
            "$volume": "volume",
        },
    )


def load_sh000300_benchmark_returns(
    config: DirectQlibExportConfig,
    *,
    qlib_module: Any | None = None,
) -> pd.Series:
    """Load SH000300 open-to-open benchmark returns from direct Qlib features."""

    qlib_obj = qlib_module or importlib.import_module("qlib")
    _init_qlib(qlib_obj, config)
    raw = qlib_obj.data.D.features(
        instruments=[config.benchmark_symbol],
        fields=["$open"],
        start_time=config.start_date,
        end_time=config.end_date,
        freq=config.frequency,
    )
    if "$open" not in raw:
        raise MarketDataError("Qlib benchmark features missing $open.")
    benchmark_frame = _qlib_features_to_date_asset(raw[["$open"]])
    symbol = config.benchmark_symbol.upper()
    if symbol in benchmark_frame.index.get_level_values("ticker"):
        open_prices = benchmark_frame.xs(symbol, level="ticker")["$open"].copy()
    else:
        open_prices = benchmark_frame["$open"].copy()
        open_prices.index = open_prices.index.get_level_values("date")
    returns = open_prices.sort_index().pct_change().dropna()
    returns.index.name = "date"
    returns.name = f"{config.benchmark_symbol}_return"
    return returns


def build_direct_qlib_source_manifest(
    *,
    config: DirectQlibExportConfig,
    probe: DirectQlibProbeResult | QlibEnvironmentStatus,
    output_paths: dict[str, str],
) -> DirectQlibSourceManifest:
    """Build a source manifest or structured blocker from direct-Qlib evidence."""

    if isinstance(probe, QlibEnvironmentStatus):
        missing_inputs = _dedupe([*probe.missing_inputs, "sh000300_benchmark_returns"])
        benchmark = QlibBenchmarkReturnEvidence(
            benchmark_symbol=config.benchmark_symbol,
            available=False,
            missing_inputs=["sh000300_benchmark_returns"],
        )
        adjustment = QlibAdjustmentEvidence(
            adjustment_status="unresolved",
            factor_field_observed=False,
            fields_observed=[],
            missing_inputs=["qlib_adjustment_factor_evidence"],
            forbidden_claims=["paper_qlib_price_parity"],
        )
        data_payload = _data_version_payload(config, probe.model_dump(mode="json"), output_paths)
        return DirectQlibSourceManifest(
            provider_uri=config.provider_uri,
            market=config.market,
            benchmark_symbol=config.benchmark_symbol,
            data_version=_payload_digest(data_payload),
            data_version_payload=data_payload,
            environment=probe,
            calendar=None,
            instruments=[],
            available_fields=[],
            adjustment=adjustment,
            benchmark_returns=benchmark,
            output_paths=output_paths,
            missing_inputs=missing_inputs,
            forbidden_claims=DIRECT_QLIB_FORBIDDEN_CLAIMS.copy(),
            claim_status="blocked_missing_direct_qlib_evidence",
            notes=[
                "Direct Qlib execution is blocked before data read.",
                "HF fallback may be used only with D-06 downgrade evidence.",
                "backtest_readiness_only remains unavailable until blockers close.",
            ],
        )

    missing_inputs = _dedupe([*probe.missing_inputs])
    forbidden_claims = DIRECT_QLIB_FORBIDDEN_CLAIMS.copy()
    claim_status = probe.claim_status
    if missing_inputs or probe.adjustment.adjustment_status == "unresolved":
        claim_status = "blocked_missing_direct_qlib_evidence"
    elif probe.benchmark_returns.available:
        claim_status = "backtest_readiness_only"
    if not probe.benchmark_returns.available and "sh000300_benchmark_returns" not in missing_inputs:
        missing_inputs.append("sh000300_benchmark_returns")
    data_payload = _data_version_payload(config, probe.model_dump(mode="json"), output_paths)
    return DirectQlibSourceManifest(
        provider_uri=config.provider_uri,
        market=config.market,
        benchmark_symbol=config.benchmark_symbol,
        data_version=_payload_digest(data_payload),
        data_version_payload=data_payload,
        environment=probe.environment,
        calendar=probe.calendar,
        instruments=probe.instruments,
        available_fields=probe.available_fields,
        adjustment=probe.adjustment,
        benchmark_returns=probe.benchmark_returns,
        output_paths=output_paths,
        missing_inputs=missing_inputs,
        forbidden_claims=forbidden_claims,
        claim_status=claim_status,
        notes=probe.notes,
    )


def build_hf_fallback_evidence(
    *,
    direct_qlib_blocker: str,
    hf_source_revision: str,
    hf_coverage_summary: dict[str, Any],
    paper_target_gap: str,
) -> dict[str, Any]:
    """Return D-06 downgrade evidence for the HF engineering fallback."""

    return {
        "data_source": "hf_quantaalpha_qlib_csi300",
        "direct_qlib_blocker": direct_qlib_blocker,
        "hf_source_revision": hf_source_revision,
        "hf_coverage_summary": hf_coverage_summary,
        "paper_target_gap": paper_target_gap,
        "claim_status": "scaled_real_runtime_engineering_evidence_only",
        "downgrade_status": "engineering_fallback",
        "forbidden_claims": [
            "full_paper_reproduction",
            "qlib_equivalent_backtest_evidence",
            "paper_protocol_data_authority",
        ],
        "decision_ids": ["D-03", "D-06"],
    }


def _qlib_features_to_date_asset(raw: pd.DataFrame) -> pd.DataFrame:
    """Convert Qlib `D.features()` output to the project `(date, asset)` contract."""

    if not isinstance(raw, pd.DataFrame):
        raise MarketDataError("Qlib features output must be a pandas DataFrame.")
    if not isinstance(raw.index, pd.MultiIndex) or raw.index.nlevels != 2:
        raise MarketDataError("Qlib features output must use a two-level MultiIndex.")

    date_level = _resolve_qlib_index_level(raw.index, preferred_names=("datetime", "date"))
    asset_level = _resolve_qlib_index_level(
        raw.index,
        preferred_names=("instrument", "asset"),
        exclude=date_level,
    )
    if date_level == asset_level:
        raise MarketDataError("Qlib features index must have distinct date and asset levels.")

    date_values = pd.to_datetime(raw.index.get_level_values(date_level), errors="raise")
    asset_values = [str(item).upper() for item in raw.index.get_level_values(asset_level)]
    frame = raw.copy()
    frame.index = pd.MultiIndex.from_arrays(
        [date_values, asset_values],
        names=["date", "ticker"],
    )
    return frame.sort_index()


def _resolve_qlib_index_level(
    index: pd.MultiIndex,
    *,
    preferred_names: tuple[str, ...],
    exclude: int | None = None,
) -> int:
    for name in preferred_names:
        if name in index.names:
            position = list(index.names).index(name)
            if position != exclude:
                return position

    candidates = [level for level in range(index.nlevels) if level != exclude]
    if not candidates:
        raise MarketDataError("Qlib features index level could not be resolved.")
    if preferred_names[0] in {"datetime", "date"}:
        return max(
            candidates,
            key=lambda level: int(
                pd.to_datetime(index.get_level_values(level), errors="coerce").notna().sum()
            ),
        )
    return candidates[0]


def write_direct_qlib_source_manifest_json(
    path: str | Path,
    manifest: DirectQlibSourceManifest,
) -> None:
    """Write the direct-Qlib source manifest as stable JSON."""

    _write_json(path, manifest.model_dump(mode="json"))


def load_direct_qlib_source_manifest_json(path: str | Path) -> DirectQlibSourceManifest:
    """Load a direct-Qlib source manifest from JSON."""

    return DirectQlibSourceManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))


def render_direct_qlib_source_manifest_markdown(manifest: DirectQlibSourceManifest) -> str:
    """Render a human-readable direct-Qlib source report."""

    lines = [
        "# Phase 13 Direct Qlib Source Manifest",
        "",
        "## Source Status",
        "",
        f"- Data source: `{manifest.data_source}`",
        f"- Provider URI: `{manifest.provider_uri}`",
        f"- Market: `{manifest.market}`",
        f"- Benchmark symbol: `{manifest.benchmark_symbol}`",
        f"- Claim status: `{manifest.claim_status}`",
        f"- Data version: `{manifest.data_version}`",
        "",
        "## Environment Gate",
        "",
        f"- Python version: `{manifest.environment.python_version}`",
        f"- Python supported: `{str(manifest.environment.python_version_supported).lower()}`",
        f"- pyqlib importable: `{str(manifest.environment.pyqlib_importable).lower()}`",
        f"- Qlib version: `{manifest.environment.qlib_version}`",
        "",
        "## Backtest Readiness Evidence",
        "",
        "- Scope: backtest_readiness_only",
        f"- Calendar days: `{manifest.calendar.trading_days if manifest.calendar else 0}`",
        f"- Instruments: `{len(manifest.instruments)}`",
        f"- Available fields: {', '.join(manifest.available_fields) or 'None'}",
        f"- Benchmark returns available: `{str(manifest.benchmark_returns.available).lower()}`",
        f"- Adjustment status: `{manifest.adjustment.adjustment_status}`",
        "",
        "## HF Fallback Evidence",
        "",
        (
            "- None recorded."
            if manifest.hf_fallback_evidence is None
            else f"- Claim status: `{manifest.hf_fallback_evidence['claim_status']}`"
        ),
        "",
        "## Missing Inputs",
        "",
        *_bullet_lines(manifest.missing_inputs),
        "",
        "## Forbidden Claims",
        "",
        *_bullet_lines(manifest.forbidden_claims),
        "",
        "## Decision Evidence",
        "",
        *_bullet_lines(manifest.decision_ids),
        "",
    ]
    return "\n".join(lines)


def write_direct_qlib_source_manifest_markdown(
    path: str | Path,
    manifest: DirectQlibSourceManifest,
) -> None:
    """Write a human-readable direct-Qlib source report."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_direct_qlib_source_manifest_markdown(manifest), encoding="utf-8")


def write_benchmark_returns_manifest_json(
    path: str | Path,
    manifest: DirectQlibSourceManifest,
) -> None:
    """Write a compact SH000300 benchmark-return manifest."""

    payload = {
        "manifest_id": "phase13-sh000300-benchmark-returns",
        "data_source": manifest.data_source,
        "provider_uri": manifest.provider_uri,
        "benchmark_symbol": manifest.benchmark_symbol,
        "data_version": manifest.data_version,
        "benchmark_returns": manifest.benchmark_returns.model_dump(mode="json"),
        "missing_inputs": manifest.benchmark_returns.missing_inputs,
        "claim_status": manifest.claim_status,
        "forbidden_claims": manifest.forbidden_claims,
        "decision_ids": ["D-07", "D-09"],
    }
    _write_json(path, payload)


def _init_qlib(qlib_module: Any, config: DirectQlibExportConfig) -> None:
    init = getattr(qlib_module, "init", None)
    if init is None:
        return
    kwargs: dict[str, Any] = {}
    if config.provider_uri:
        kwargs["provider_uri"] = config.provider_uri
    kwargs["region"] = "cn"
    init(**kwargs)


def _build_adjustment_evidence(fields: list[str]) -> QlibAdjustmentEvidence:
    if "$factor" in fields:
        return QlibAdjustmentEvidence(
            adjustment_status="observed_factor_field",
            factor_field_observed=True,
            fields_observed=fields,
            missing_inputs=[],
            forbidden_claims=[
                "full_paper_reproduction",
                "qlib_equivalent_backtest_evidence",
            ],
            notes=[
                "$factor is present for later original-price or adjustment inspection.",
                "Phase 13 does not claim formal price parity.",
            ],
        )
    return QlibAdjustmentEvidence(
        adjustment_status="unresolved",
        factor_field_observed=False,
        fields_observed=fields,
        missing_inputs=["qlib_adjustment_factor_evidence"],
        forbidden_claims=[
            "paper_qlib_price_parity",
            "qlib_equivalent_backtest_evidence",
        ],
        notes=["Qlib factor/original-price reconstruction evidence is missing."],
    )


def _benchmark_evidence_from_series(
    benchmark_symbol: str,
    returns: pd.Series,
    *,
    path: str | None = None,
) -> QlibBenchmarkReturnEvidence:
    if returns.empty:
        return QlibBenchmarkReturnEvidence(
            benchmark_symbol=benchmark_symbol,
            available=False,
            missing_inputs=["sh000300_benchmark_returns"],
            path=path,
        )
    return QlibBenchmarkReturnEvidence(
        benchmark_symbol=benchmark_symbol,
        available=True,
        rows=len(returns),
        start_date=_date_text(returns.index.min()),
        end_date=_date_text(returns.index.max()),
        path=path,
        missing_inputs=[],
    )


def _data_version_payload(
    config: DirectQlibExportConfig,
    probe_payload: dict[str, Any],
    output_paths: dict[str, str],
) -> dict[str, Any]:
    return {
        "provider_uri": config.provider_uri,
        "market": config.market,
        "benchmark_symbol": config.benchmark_symbol,
        "start_date": config.start_date,
        "end_date": config.end_date,
        "frequency": config.frequency,
        "probe": probe_payload,
        "output_paths": output_paths,
        "scope": "backtest_readiness_only",
    }


def _payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _date_text(value: Any) -> str:
    return str(pd.Timestamp(value).date())


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _bullet_lines(items: list[str]) -> list[str]:
    if not items:
        return ["- None recorded."]
    return [f"- {item}" for item in items]
