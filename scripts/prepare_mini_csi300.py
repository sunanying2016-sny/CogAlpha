"""Create a source-backed mini CSI300 prepared dataset from local parquet artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from cogalpha.data import compute_forward_returns, normalize_ohlcv_panel


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a 10-asset x 30-day CSI300 mini split.")
    parser.add_argument("--source-dir", default="data/processed/csi300")
    parser.add_argument("--output-dir", default="data/processed/csi300-mini-10x30")
    parser.add_argument("--assets", type=int, default=10)
    parser.add_argument("--visible-days", type=int, default=30)
    parser.add_argument("--horizon-days", type=int, default=10)
    parser.add_argument("--trade-delay-days", type=int, default=1)
    parser.add_argument("--price-column", default="open")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    output_dir = Path(args.output_dir)
    metadata = json.loads((source_dir / "metadata.json").read_text(encoding="utf-8"))

    lookahead_days = args.horizon_days + args.trade_delay_days
    required_days = args.visible_days + lookahead_days
    split_sources = {
        name: normalize_ohlcv_panel(pd.read_parquet(source_dir / f"{name}_ohlcv.parquet"))
        for name in ("train", "valid", "test")
    }
    split_windows = {
        name: _window(panel, required_days)
        for name, panel in split_sources.items()
    }
    selected_assets = _common_complete_assets(split_sources, split_windows, args.assets)

    output_dir.mkdir(parents=True, exist_ok=True)
    split_metadata: dict[str, dict[str, str | int]] = {}
    visible_panels: list[pd.DataFrame] = []
    for name, source_panel in split_sources.items():
        extended_dates = split_windows[name]
        visible_dates = extended_dates[: args.visible_days]
        extended = _slice_assets_dates(source_panel, selected_assets, extended_dates)
        visible = _slice_assets_dates(source_panel, selected_assets, visible_dates)
        forward_returns = compute_forward_returns(
            extended,
            horizon_days=args.horizon_days,
            price_column=args.price_column,
            trade_delay_days=args.trade_delay_days,
        ).loc[visible_dates, selected_assets]

        visible.reset_index().to_parquet(output_dir / f"{name}_ohlcv.parquet", index=False)
        forward_returns.to_parquet(output_dir / f"{name}_forward_returns.parquet")
        visible_panels.append(visible)
        split_metadata[name] = {
            "ohlcv": str(output_dir / f"{name}_ohlcv.parquet"),
            "forward_returns": str(output_dir / f"{name}_forward_returns.parquet"),
            "rows": int(len(visible)),
            "dates": int(len(visible_dates)),
            "assets": int(len(selected_assets)),
            "actual_start": str(visible_dates[0].date()),
            "actual_end": str(visible_dates[-1].date()),
            "label_lookahead_rows": int(lookahead_days),
            "extended_start": str(extended_dates[0].date()),
            "extended_end": str(extended_dates[-1].date()),
            "non_null_forward_returns": int(forward_returns.notna().sum().sum()),
        }

    full_panel = pd.concat(visible_panels).sort_index()
    full_panel.reset_index().to_parquet(output_dir / "ohlcv_panel.parquet", index=False)
    source_payload = {
        "source_data_version": metadata["data_version"],
        "source_repo_id": metadata["source_repo_id"],
        "source_repo_sha": metadata["source_repo_sha"],
        "assets": selected_assets,
        "visible_days": args.visible_days,
        "horizon_days": args.horizon_days,
        "trade_delay_days": args.trade_delay_days,
        "price_column": args.price_column,
        "split_windows": split_metadata,
    }
    mini_version = hashlib.sha256(
        json.dumps(source_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    mini_metadata = {
        **metadata,
        "prepared_at": datetime.now(UTC).isoformat(),
        "data_version": mini_version,
        "data_version_payload": source_payload,
        "source_prepared_dir": str(source_dir),
        "mini_subset": {
            "assets": selected_assets,
            "visible_days": args.visible_days,
            "label_lookahead_rows": lookahead_days,
            "rows_per_split": args.assets * args.visible_days,
            "purpose": "small live LLM workflow verification, not performance evidence",
        },
        "full_panel": {
            "path": str(output_dir / "ohlcv_panel.parquet"),
            "rows": int(len(full_panel)),
            "dates": int(full_panel.index.get_level_values("date").nunique()),
            "assets": int(len(selected_assets)),
            "start": str(full_panel.index.get_level_values("date").min().date()),
            "end": str(full_panel.index.get_level_values("date").max().date()),
        },
        "splits": split_metadata,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(mini_metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(mini_metadata, indent=2, sort_keys=True))


def _window(panel: pd.DataFrame, required_days: int) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(panel.index.get_level_values("date").unique()).sort_values()
    if len(dates) < required_days:
        raise ValueError(f"Need {required_days} dates, found {len(dates)}")
    return dates[:required_days]


def _common_complete_assets(
    split_sources: dict[str, pd.DataFrame],
    split_windows: dict[str, pd.DatetimeIndex],
    asset_count: int,
) -> list[str]:
    common: set[str] | None = None
    for name, panel in split_sources.items():
        dates = split_windows[name]
        sliced = panel.loc[panel.index.get_level_values("date").isin(dates)]
        complete = []
        for asset, asset_panel in sliced.groupby(level="ticker", sort=True):
            if len(asset_panel) != len(dates):
                continue
            if asset_panel.isna().any().any():
                continue
            complete.append(str(asset))
        assets = set(complete)
        common = assets if common is None else common & assets

    selected = sorted(common or [])[:asset_count]
    if len(selected) < asset_count:
        raise ValueError(f"Found only {len(selected)} complete common assets")
    return selected


def _slice_assets_dates(
    panel: pd.DataFrame,
    assets: list[str],
    dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    date_values = panel.index.get_level_values("date")
    asset_values = panel.index.get_level_values("ticker").astype(str)
    return panel.loc[date_values.isin(dates) & asset_values.isin(assets)].sort_index()


if __name__ == "__main__":
    main()
