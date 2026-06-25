"""Download and normalize the Hugging Face QuantaAlpha Qlib CSI300 dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from huggingface_hub import hf_hub_download, repo_info

from cogalpha.config import BaselineExperimentConfig
from cogalpha.data import (
    build_baseline_market_data,
    filter_panel_by_instrument_ranges,
    load_qlib_daily_pv_hdf,
    load_qlib_instrument_ranges,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare QuantaAlpha/qlib_csi300 for CogAlpha evaluation."
    )
    parser.add_argument("--repo-id", default="QuantaAlpha/qlib_csi300")
    parser.add_argument("--filename", default="daily_pv.h5")
    parser.add_argument("--output-dir", default="data/processed/csi300")
    parser.add_argument("--raw-dir", default="data/raw/hf_qlib_csi300")
    parser.add_argument("--keep-index-instruments", action="store_true")
    parser.add_argument("--skip-universe-filter", action="store_true")
    parser.add_argument("--universe-zip", default="cn_data.zip")
    parser.add_argument("--universe-file", default="cn_data/instruments/csi300.txt")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    raw_dir = Path(args.raw_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    source_info = repo_info(args.repo_id, repo_type="dataset")

    raw_path = hf_hub_download(
        repo_id=args.repo_id,
        filename=args.filename,
        repo_type="dataset",
        local_dir=raw_dir,
    )
    panel = load_qlib_daily_pv_hdf(
        raw_path,
        drop_index_instruments=not args.keep_index_instruments,
    )

    universe_zip_path = None
    universe_ranges = []
    if not args.skip_universe_filter:
        universe_zip_path = hf_hub_download(
            repo_id=args.repo_id,
            filename=args.universe_zip,
            repo_type="dataset",
            local_dir=raw_dir,
        )
        universe_ranges = load_qlib_instrument_ranges(
            universe_zip_path,
            instrument_file=args.universe_file,
        )
        panel = filter_panel_by_instrument_ranges(panel, universe_ranges)

    dataset = build_baseline_market_data(panel, BaselineExperimentConfig())

    panel_reset = panel.reset_index()
    panel_path = output_dir / "ohlcv_panel.parquet"
    panel_reset.to_parquet(panel_path, index=False)

    split_paths: dict[str, dict[str, str]] = {}
    for split_name in ("train", "valid", "test"):
        split = dataset.split(split_name)
        split_dates = split.ohlcv_panel.index.get_level_values("date")
        non_null_returns = int(split.forward_returns.notna().sum().sum())
        ohlcv_path = output_dir / f"{split_name}_ohlcv.parquet"
        returns_path = output_dir / f"{split_name}_forward_returns.parquet"
        split.ohlcv_panel.reset_index().to_parquet(ohlcv_path, index=False)
        split.forward_returns.to_parquet(returns_path)
        split_paths[split_name] = {
            "ohlcv": str(ohlcv_path),
            "forward_returns": str(returns_path),
            "rows": str(len(split.ohlcv_panel)),
            "dates": str(split.ohlcv_panel.index.get_level_values("date").nunique()),
            "assets": str(split.ohlcv_panel.index.get_level_values("ticker").nunique()),
            "actual_start": str(split_dates.min().date()),
            "actual_end": str(split_dates.max().date()),
            "non_null_forward_returns": str(non_null_returns),
        }

    dates = panel.index.get_level_values("date")
    assets = panel.index.get_level_values("ticker")
    data_version_payload = {
        "source_repo_id": args.repo_id,
        "source_repo_sha": source_info.sha,
        "source_filename": args.filename,
        "horizon_days": dataset.horizon_days,
        "return_price_column": BaselineExperimentConfig().return_price_column,
        "trade_delay_days": BaselineExperimentConfig().trade_delay_days,
        "drop_index_instruments": not args.keep_index_instruments,
        "universe_file": None if args.skip_universe_filter else args.universe_file,
        "split": BaselineExperimentConfig().split.model_dump(mode="json"),
        "fitness_gate": BaselineExperimentConfig().fitness_gate.model_dump(mode="json"),
    }
    data_version = hashlib.sha256(
        json.dumps(data_version_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    metadata = {
        "prepared_at": datetime.now(UTC).isoformat(),
        "source_repo_id": args.repo_id,
        "source_repo_sha": source_info.sha,
        "source_filename": args.filename,
        "raw_path": str(raw_path),
        "data_version": data_version,
        "data_version_payload": data_version_payload,
        "dataset": dataset.dataset,
        "horizon_days": dataset.horizon_days,
        "return_price_column": BaselineExperimentConfig().return_price_column,
        "trade_delay_days": BaselineExperimentConfig().trade_delay_days,
        "paper_settings": {
            "dataset": "CSI300",
            "frequency": "daily",
            "input_columns": ["open", "high", "low", "close", "volume"],
            "target": "10-day forward return with buying and selling at open price",
            "return_price_column": "open",
            "trade_delay_days": 1,
            "timing_contract": (
                "AlphaCandidate observes date t daily OHLCV, enters at the next open, "
                "and exits after 10 trading opens."
            ),
            "train": ["2011-01-01", "2019-12-31"],
            "valid": ["2020-01-01", "2020-12-31"],
            "test": ["2021-01-01", "2024-12-01"],
            "fitness_gate": {
                "qualified_percentile": 0.65,
                "elite_percentile": 0.80,
                "qualified_minima": {
                    "ic": 0.005,
                    "rank_ic": 0.005,
                    "icir": 0.05,
                    "rank_icir": 0.05,
                    "mi": 0.02,
                },
                "elite_minima": {
                    "ic": 0.01,
                    "rank_ic": 0.01,
                    "icir": 0.1,
                    "rank_icir": 0.1,
                    "mi": 0.02,
                },
            },
        },
        "drop_index_instruments": not args.keep_index_instruments,
        "universe_filter": None
        if args.skip_universe_filter
        else {
            "zip_path": str(universe_zip_path),
            "instrument_file": args.universe_file,
            "ranges": len(universe_ranges),
            "unique_assets": len({item.asset for item in universe_ranges}),
        },
        "full_panel": {
            "path": str(panel_path),
            "rows": len(panel),
            "dates": dates.nunique(),
            "assets": assets.nunique(),
            "start": str(dates.min().date()),
            "end": str(dates.max().date()),
        },
        "splits": split_paths,
    }
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
