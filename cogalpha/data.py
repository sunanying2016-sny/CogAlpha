"""Market data loading and split-safe forward-return construction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zipfile import ZipFile

import numpy as np
import pandas as pd

from cogalpha.alpha_contract import DEFAULT_OHLCV_COLUMNS
from cogalpha.config import BaselineExperimentConfig, SplitConfig

DataSplitName = Literal["train", "valid", "test"]


class MarketDataError(ValueError):
    """Raised when market data does not satisfy the OHLCV Input contract."""


@dataclass(frozen=True)
class MarketDataSplit:
    """One chronological split with OHLCV Input and aligned forward returns."""

    name: DataSplitName
    ohlcv_panel: pd.DataFrame
    forward_returns: pd.DataFrame


@dataclass(frozen=True)
class BaselineMarketData:
    """Baseline Experiment data prepared for panel-backed fitness evaluation."""

    dataset: str
    horizon_days: int
    full_ohlcv_panel: pd.DataFrame
    train: MarketDataSplit
    valid: MarketDataSplit
    test: MarketDataSplit

    def split(self, name: DataSplitName) -> MarketDataSplit:
        """Return one prepared chronological split by name."""

        return getattr(self, name)


@dataclass(frozen=True)
class QlibInstrumentRange:
    """One point-in-time Qlib universe membership interval."""

    asset: str
    start: pd.Timestamp
    end: pd.Timestamp


def load_ohlcv_panel(
    path: str | Path,
    *,
    date_column: str = "date",
    asset_column: str = "asset",
    column_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Load OHLCV data from CSV or Parquet and normalize it to the panel contract."""

    data_path = Path(path)
    if not data_path.exists():
        raise MarketDataError(f"Market data path does not exist: {data_path}")

    suffix = data_path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        raw = pd.read_csv(data_path)
    elif suffix in {".parquet", ".pq"}:
        raw = pd.read_parquet(data_path)
    else:
        raise MarketDataError(
            "Unsupported market data format. Expected .csv, .txt, .parquet, or .pq."
        )

    return normalize_ohlcv_panel(
        raw,
        date_column=date_column,
        asset_column=asset_column,
        column_map=column_map,
    )


def load_qlib_daily_pv_hdf(
    path: str | Path,
    *,
    key: str = "data",
    drop_index_instruments: bool = True,
) -> pd.DataFrame:
    """Load QuantaAlpha/Qlib daily price-volume HDF5 data as OHLCV Input.

    The Hugging Face `QuantaAlpha/qlib_csi300` files use Qlib-style names:
    MultiIndex levels `datetime` and `instrument`, and columns prefixed with `$`.
    """

    data_path = Path(path)
    if not data_path.exists():
        raise MarketDataError(f"Qlib HDF5 path does not exist: {data_path}")

    raw = pd.read_hdf(data_path, key=key)
    panel = normalize_ohlcv_panel(
        raw,
        column_map={
            "$open": "open",
            "$high": "high",
            "$low": "low",
            "$close": "close",
            "$volume": "volume",
        },
    )

    if drop_index_instruments:
        assets = panel.index.get_level_values("ticker").astype(str)
        is_index = assets.str.startswith(("SH000", "SZ399"))
        panel = panel.loc[~is_index].copy()
        if panel.empty:
            raise MarketDataError("Qlib HDF5 contains no stock instruments after index filtering.")

    return panel


def load_qlib_instrument_ranges(
    path: str | Path,
    *,
    instrument_file: str = "cn_data/instruments/csi300.txt",
) -> list[QlibInstrumentRange]:
    """Load Qlib point-in-time instrument membership ranges from txt or zip."""

    data_path = Path(path)
    if not data_path.exists():
        raise MarketDataError(f"Qlib instrument path does not exist: {data_path}")

    if data_path.suffix.lower() == ".zip":
        with ZipFile(data_path) as archive:
            try:
                text = archive.read(instrument_file).decode("utf-8")
            except KeyError:
                raise MarketDataError(
                    f"Instrument file not found in zip: {instrument_file}"
                ) from None
    else:
        text = data_path.read_text(encoding="utf-8")

    ranges: list[QlibInstrumentRange] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) != 3:
            raise MarketDataError(
                f"Invalid instrument range at line {line_number}: expected 3 fields."
            )
        asset, start, end = parts
        ranges.append(
            QlibInstrumentRange(
                asset=asset.upper(),
                start=pd.Timestamp(start),
                end=pd.Timestamp(end),
            )
        )

    if not ranges:
        raise MarketDataError(f"Instrument file contains no ranges: {instrument_file}")
    return ranges


def filter_panel_by_instrument_ranges(
    ohlcv_panel: pd.DataFrame,
    ranges: list[QlibInstrumentRange],
) -> pd.DataFrame:
    """Filter an OHLCV panel to Qlib point-in-time instrument membership ranges."""

    panel = validate_ohlcv_panel(ohlcv_panel)
    ranges_by_asset: dict[str, list[QlibInstrumentRange]] = {}
    for item in ranges:
        ranges_by_asset.setdefault(item.asset, []).append(item)

    filtered_assets: list[pd.DataFrame] = []
    for asset, asset_panel in panel.groupby(level="ticker", sort=False):
        asset_ranges = ranges_by_asset.get(str(asset).upper())
        if not asset_ranges:
            continue
        dates = asset_panel.index.get_level_values("date")
        mask = np.zeros(len(asset_panel), dtype=bool)
        for item in asset_ranges:
            mask |= (dates >= item.start) & (dates <= item.end)
        if mask.any():
            filtered_assets.append(asset_panel.loc[mask])

    if not filtered_assets:
        raise MarketDataError("No OHLCV rows matched the supplied instrument ranges.")
    return pd.concat(filtered_assets).sort_index()


def normalize_ohlcv_panel(
    raw: pd.DataFrame,
    *,
    date_column: str = "date",
    asset_column: str = "asset",
    column_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Normalize flat or MultiIndex OHLCV data to `(date, ticker)` MultiIndex form."""

    if not isinstance(raw, pd.DataFrame):
        raise MarketDataError("OHLCV input must be a pandas DataFrame.")

    frame = raw.copy()
    if column_map:
        frame = frame.rename(columns=column_map)

    if isinstance(frame.index, pd.MultiIndex) and frame.index.nlevels == 2:
        date_values = pd.to_datetime(frame.index.get_level_values(0), errors="coerce")
        asset_values = frame.index.get_level_values(1).astype(str)
        frame.index = pd.MultiIndex.from_arrays(
            [date_values, asset_values],
            names=["date", "ticker"],
        )
        panel = frame
    else:
        # The instrument column is `ticker` under the O-2 contract; accept the
        # legacy `asset` header too. When the caller leaves `asset_column` at its
        # default and only a `ticker` column is present (e.g. a prepared artifact
        # round-tripped from a (date, ticker) index), use `ticker`.
        instrument_column = asset_column
        if (
            asset_column == "asset"
            and "asset" not in frame.columns
            and "ticker" in frame.columns
        ):
            instrument_column = "ticker"

        missing_keys = [
            column for column in (date_column, instrument_column) if column not in frame
        ]
        if missing_keys:
            raise MarketDataError(f"OHLCV data is missing key columns: {missing_keys}.")

        frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
        frame[instrument_column] = frame[instrument_column].astype(str)
        panel = frame.set_index([date_column, instrument_column])
        panel.index.names = ["date", "ticker"]

    return validate_ohlcv_panel(panel)


def validate_ohlcv_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """Validate and return a sorted copy of an OHLCV panel."""

    if not isinstance(panel, pd.DataFrame):
        raise MarketDataError("OHLCV panel must be a pandas DataFrame.")
    if not isinstance(panel.index, pd.MultiIndex) or panel.index.nlevels != 2:
        raise MarketDataError("OHLCV panel must use a two-level MultiIndex: date, ticker.")

    normalized = panel.copy()
    date_values = pd.to_datetime(normalized.index.get_level_values(0), errors="coerce")
    asset_values = normalized.index.get_level_values(1).astype(str)
    if date_values.isna().any():
        raise MarketDataError("OHLCV panel contains invalid dates.")
    if any(asset == "" for asset in asset_values):
        raise MarketDataError("OHLCV panel contains empty ticker identifiers.")

    normalized.index = pd.MultiIndex.from_arrays(
        [date_values, asset_values],
        names=["date", "ticker"],
    )
    if normalized.index.has_duplicates:
        raise MarketDataError("OHLCV panel contains duplicate (date, ticker) rows.")

    missing_columns = [column for column in DEFAULT_OHLCV_COLUMNS if column not in normalized]
    if missing_columns:
        raise MarketDataError(f"OHLCV panel is missing required columns: {missing_columns}.")

    normalized = normalized.loc[:, list(DEFAULT_OHLCV_COLUMNS)].sort_index()
    for column in DEFAULT_OHLCV_COLUMNS:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    if normalized.empty:
        raise MarketDataError("OHLCV panel is empty.")
    return normalized


def compute_forward_returns(
    ohlcv_panel: pd.DataFrame,
    *,
    horizon_days: int,
    price_column: str = "close",
    trade_delay_days: int = 0,
) -> pd.DataFrame:
    """Compute per-asset forward returns aligned to the current observation date.

    `trade_delay_days=1` means a signal formed with date `t` OHLCV is traded at
    the next observation's open and sold `horizon_days` trading observations later.
    """

    if horizon_days < 1:
        raise MarketDataError("Forward-return horizon must be at least 1 day.")
    if trade_delay_days < 0:
        raise MarketDataError("Trade delay must be non-negative.")

    panel = validate_ohlcv_panel(ohlcv_panel)
    if price_column not in panel:
        raise MarketDataError(f"Price column {price_column!r} is not present in OHLCV panel.")

    prices = panel[price_column]
    grouped = prices.groupby(level="ticker", sort=False)
    entry_prices = grouped.shift(-trade_delay_days) if trade_delay_days else prices
    exit_prices = grouped.shift(-(trade_delay_days + horizon_days))
    forward_returns = (exit_prices / entry_prices) - 1.0
    return forward_returns.unstack("ticker").sort_index().sort_index(axis=1)


def slice_ohlcv_panel(
    ohlcv_panel: pd.DataFrame,
    *,
    start: object,
    end: object,
) -> pd.DataFrame:
    """Return an inclusive date slice from an OHLCV panel."""

    panel = validate_ohlcv_panel(ohlcv_panel)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts > end_ts:
        raise MarketDataError(f"Split start {start_ts.date()} is after end {end_ts.date()}.")

    dates = panel.index.get_level_values("date")
    return panel.loc[(dates >= start_ts) & (dates <= end_ts)].copy()


def build_baseline_market_data(
    ohlcv_panel: pd.DataFrame,
    config: BaselineExperimentConfig | None = None,
) -> BaselineMarketData:
    """Prepare split-safe OHLCV panels and forward returns for the Baseline Experiment."""

    experiment = config or BaselineExperimentConfig()
    _validate_split_order(experiment.split)
    full_panel = validate_ohlcv_panel(ohlcv_panel)

    train = _build_split(
        "train",
        full_panel,
        start=experiment.split.train_start,
        end=experiment.split.train_end,
        horizon_days=experiment.horizon_days,
        price_column=experiment.return_price_column,
        trade_delay_days=experiment.trade_delay_days,
    )
    valid = _build_split(
        "valid",
        full_panel,
        start=experiment.split.valid_start,
        end=experiment.split.valid_end,
        horizon_days=experiment.horizon_days,
        price_column=experiment.return_price_column,
        trade_delay_days=experiment.trade_delay_days,
    )
    test = _build_split(
        "test",
        full_panel,
        start=experiment.split.test_start,
        end=experiment.split.test_end,
        horizon_days=experiment.horizon_days,
        price_column=experiment.return_price_column,
        trade_delay_days=experiment.trade_delay_days,
    )

    return BaselineMarketData(
        dataset=experiment.dataset,
        horizon_days=experiment.horizon_days,
        full_ohlcv_panel=full_panel,
        train=train,
        valid=valid,
        test=test,
    )


def load_baseline_market_data(
    path: str | Path,
    config: BaselineExperimentConfig | None = None,
    *,
    date_column: str = "date",
    asset_column: str = "asset",
    column_map: dict[str, str] | None = None,
) -> BaselineMarketData:
    """Load and prepare Baseline Experiment market data from a local file."""

    panel = load_ohlcv_panel(
        path,
        date_column=date_column,
        asset_column=asset_column,
        column_map=column_map,
    )
    return build_baseline_market_data(panel, config=config)


def load_prepared_baseline_market_data(
    directory: str | Path,
    config: BaselineExperimentConfig | None = None,
) -> BaselineMarketData:
    """Load prepared Baseline Experiment parquet artifacts from a directory."""

    prepared_dir = Path(directory)
    if not prepared_dir.exists():
        raise MarketDataError(f"Prepared market data directory does not exist: {prepared_dir}")

    experiment = config or BaselineExperimentConfig()
    full_panel = _read_prepared_ohlcv(prepared_dir / "ohlcv_panel.parquet")
    train = MarketDataSplit(
        name="train",
        ohlcv_panel=_read_prepared_ohlcv(prepared_dir / "train_ohlcv.parquet"),
        forward_returns=_read_prepared_forward_returns(
            prepared_dir / "train_forward_returns.parquet"
        ),
    )
    valid = MarketDataSplit(
        name="valid",
        ohlcv_panel=_read_prepared_ohlcv(prepared_dir / "valid_ohlcv.parquet"),
        forward_returns=_read_prepared_forward_returns(
            prepared_dir / "valid_forward_returns.parquet"
        ),
    )
    test = MarketDataSplit(
        name="test",
        ohlcv_panel=_read_prepared_ohlcv(prepared_dir / "test_ohlcv.parquet"),
        forward_returns=_read_prepared_forward_returns(
            prepared_dir / "test_forward_returns.parquet"
        ),
    )
    return BaselineMarketData(
        dataset=experiment.dataset,
        horizon_days=experiment.horizon_days,
        full_ohlcv_panel=full_panel,
        train=train,
        valid=valid,
        test=test,
    )


def _read_prepared_ohlcv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise MarketDataError(f"Prepared OHLCV artifact is missing: {path}")
    return normalize_ohlcv_panel(pd.read_parquet(path))


def _read_prepared_forward_returns(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise MarketDataError(f"Prepared forward-return artifact is missing: {path}")
    returns = pd.read_parquet(path)
    returns.index = pd.to_datetime(returns.index, errors="raise")
    return returns.sort_index().sort_index(axis=1)


def _build_split(
    name: DataSplitName,
    full_panel: pd.DataFrame,
    *,
    start: object,
    end: object,
    horizon_days: int,
    price_column: str,
    trade_delay_days: int,
) -> MarketDataSplit:
    split_panel = slice_ohlcv_panel(full_panel, start=start, end=end)
    if split_panel.empty:
        raise MarketDataError(f"{name} split contains no OHLCV rows.")

    forward_returns = compute_forward_returns(
        split_panel,
        horizon_days=horizon_days,
        price_column=price_column,
        trade_delay_days=trade_delay_days,
    )
    if forward_returns.dropna(how="all").empty:
        raise MarketDataError(
            f"{name} split contains no usable {horizon_days}-day forward returns."
        )

    return MarketDataSplit(name=name, ohlcv_panel=split_panel, forward_returns=forward_returns)


def _validate_split_order(split: SplitConfig) -> None:
    if not (
        split.train_start <= split.train_end
        < split.valid_start <= split.valid_end
        < split.test_start <= split.test_end
    ):
        raise MarketDataError(
            "Split dates must be ordered and non-overlapping: "
            "train_start <= train_end < valid_start <= valid_end < test_start <= test_end."
        )
