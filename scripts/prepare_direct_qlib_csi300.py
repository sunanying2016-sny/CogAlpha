"""Prepare direct-Qlib CSI300 source evidence or fail-closed Phase 13 blockers."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from cogalpha.data_sources.qlib_direct import (
    DirectQlibExportConfig,
    build_direct_qlib_source_manifest,
    build_hf_fallback_evidence,
    check_direct_qlib_environment,
    extract_qlib_ohlcv_panel,
    load_sh000300_benchmark_returns,
    probe_direct_qlib_source,
    write_benchmark_returns_manifest_json,
    write_direct_qlib_source_manifest_json,
    write_direct_qlib_source_manifest_markdown,
)

PHASE13_DIR = Path(".planning/phases/13-real-data-source-universe-and-benchmark-provenance")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the direct-Qlib preparation CLI parser."""

    parser = argparse.ArgumentParser(
        description="Prepare direct-Qlib CSI300 data evidence for Phase 13."
    )
    parser.add_argument("--provider-uri", default=None)
    parser.add_argument("--market", default="csi300")
    parser.add_argument("--benchmark-symbol", default="SH000300")
    parser.add_argument("--output-dir", default="data/processed/direct_qlib_csi300")
    parser.add_argument(
        "--manifest-dir",
        default=None,
        help=(
            "Directory for the canonical 13-DIRECT-QLIB-SOURCE.json / "
            "13-BENCHMARK-RETURNS-MANIFEST.json (defaults to --output-dir). Set to "
            "PHASE13_DIR to keep one source of truth that downstream Phase 13/14/15 "
            "builders read by default (D3 path-mismatch fix)."
        ),
    )
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--allow-blocker", action="store_true")
    parser.add_argument("--allow-hf-fallback", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--no-network-download", action="store_true")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    import_module: Callable[[str], Any] | None = None,
    qlib_module: Any | None = None,
) -> None:
    """Run the CLI and print generated artifact paths only."""

    generated = run_direct_qlib_preparation(
        argv,
        import_module=import_module,
        qlib_module=qlib_module,
    )
    for path in generated:
        print(path)


def run_direct_qlib_preparation(
    argv: list[str] | None = None,
    *,
    import_module: Callable[[str], Any] | None = None,
    qlib_module: Any | None = None,
) -> list[str]:
    """Generate direct-Qlib artifacts and return generated paths."""

    args = build_arg_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # D3: the canonical source/benchmark manifests are written to --manifest-dir so the
    # freshly-exported evidence is the SAME copy every downstream Phase 13/14/15 builder
    # reads by default. Defaults to --output-dir to preserve standalone behaviour.
    manifest_dir = Path(args.manifest_dir) if args.manifest_dir else output_dir
    manifest_dir.mkdir(parents=True, exist_ok=True)
    config = DirectQlibExportConfig(
        provider_uri=args.provider_uri,
        market=args.market,
        benchmark_symbol=args.benchmark_symbol,
        output_dir=str(output_dir),
        start_date=args.start_date,
        end_date=args.end_date,
        no_network_download=args.no_network_download,
    )

    source_json = manifest_dir / "13-DIRECT-QLIB-SOURCE.json"
    source_markdown = manifest_dir / "13-DIRECT-QLIB-SOURCE.md"
    benchmark_manifest_json = manifest_dir / "13-BENCHMARK-RETURNS-MANIFEST.json"

    environment = check_direct_qlib_environment(import_module=import_module)
    output_paths: dict[str, str] = {}

    if environment.blocked:
        if not args.allow_blocker:
            raise SystemExit("Direct Qlib evidence is blocked; rerun with --allow-blocker.")
        manifest = build_direct_qlib_source_manifest(
            config=config,
            probe=environment,
            output_paths=output_paths,
        )
    else:
        resolved_qlib = qlib_module
        if resolved_qlib is None:
            importer = import_module
            if importer is None:
                import importlib

                importer = importlib.import_module
            resolved_qlib = importer("qlib")

        probe = probe_direct_qlib_source(config, qlib_module=resolved_qlib)
        if not args.probe_only:
            output_paths.update(_write_export_tables(output_dir, config, resolved_qlib))
            if "benchmark_returns" in output_paths:
                probe = probe.model_copy(
                    update={
                        "benchmark_returns": probe.benchmark_returns.model_copy(
                            update={"path": output_paths["benchmark_returns"]}
                        )
                    }
                )
        manifest = build_direct_qlib_source_manifest(
            config=config,
            probe=probe,
            output_paths=output_paths,
        )
        if manifest.missing_inputs and not args.allow_blocker:
            raise SystemExit("Direct Qlib probe has missing evidence; rerun with --allow-blocker.")

    if args.allow_hf_fallback:
        manifest = manifest.model_copy(
            update={
                "hf_fallback_evidence": build_hf_fallback_evidence(
                    direct_qlib_blocker=", ".join(manifest.missing_inputs)
                    or "direct Qlib evidence not accepted",
                    hf_source_revision="unknown_without_hf_download",
                    hf_coverage_summary={"status": "not_downloaded"},
                    paper_target_gap=(
                        "HF fallback is processed engineering evidence and not "
                        "CogAlpha paper-protocol data authority."
                    ),
                )
            }
        )

    if args.strict:
        _strict_validate_manifest(manifest)

    write_direct_qlib_source_manifest_json(source_json, manifest)
    write_direct_qlib_source_manifest_markdown(source_markdown, manifest)
    write_benchmark_returns_manifest_json(benchmark_manifest_json, manifest)
    return [str(source_json), str(source_markdown), str(benchmark_manifest_json)]


def _write_export_tables(
    output_dir: Path,
    config: DirectQlibExportConfig,
    qlib_module: Any,
) -> dict[str, str]:
    panel = extract_qlib_ohlcv_panel(config, qlib_module=qlib_module)
    returns = load_sh000300_benchmark_returns(config, qlib_module=qlib_module)
    panel_path = output_dir / "ohlcv_panel.parquet"
    returns_path = output_dir / "benchmark_returns.parquet"
    calendar_path = output_dir / "calendar.json"
    instruments_path = output_dir / "instruments.json"

    panel.reset_index().to_parquet(panel_path, index=False)
    returns.to_frame().to_parquet(returns_path)

    dates = panel.index.get_level_values("date")
    assets = sorted(set(panel.index.get_level_values("ticker").astype(str)))
    calendar_payload = {
        "start": str(pd.Timestamp(dates.min()).date()),
        "end": str(pd.Timestamp(dates.max()).date()),
        "trading_days": int(dates.nunique()),
    }
    calendar_path.write_text(
        json.dumps(calendar_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    instruments_path.write_text(json.dumps(assets, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "ohlcv_panel": str(panel_path),
        "benchmark_returns": str(returns_path),
        "calendar": str(calendar_path),
        "instruments": str(instruments_path),
    }


def _strict_validate_manifest(manifest) -> None:
    if manifest.data_source != "direct_qlib":
        raise SystemExit("Strict direct-Qlib evidence must use data_source == direct_qlib.")
    if manifest.claim_status == "backtest_readiness_only" and manifest.missing_inputs:
        raise SystemExit("Strict direct-Qlib readiness cannot contain missing inputs.")
    if "qlib_equivalent_backtest_evidence" not in manifest.forbidden_claims:
        raise SystemExit("Strict direct-Qlib artifacts must forbid Qlib backtest parity claims.")
    if manifest.hf_fallback_evidence is not None:
        required = {
            "direct_qlib_blocker",
            "hf_source_revision",
            "hf_coverage_summary",
            "paper_target_gap",
            "claim_status",
            "downgrade_status",
            "forbidden_claims",
        }
        missing = sorted(required - set(manifest.hf_fallback_evidence))
        if missing:
            raise SystemExit(f"HF fallback evidence missing fields: {missing}")
        forbidden = set(manifest.hf_fallback_evidence["forbidden_claims"])
        if not {"full_paper_reproduction", "qlib_equivalent_backtest_evidence"}.issubset(
            forbidden
        ):
            raise SystemExit("HF fallback evidence is missing required forbidden claims.")


if __name__ == "__main__":
    main()
