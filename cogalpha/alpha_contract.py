"""Shared Alpha Function and OHLCV Input contract."""

from __future__ import annotations

DEFAULT_OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
RUNTIME_PANEL_INDEX_NAMES: tuple[str, str] = ("date", "ticker")
RUNTIME_OHLCV_COLUMNS: tuple[str, ...] = DEFAULT_OHLCV_COLUMNS
DEFAULT_ALPHA_LIBRARY_ALIASES: tuple[str, ...] = ("np", "pd", "stats", "talib", "math")
DEFAULT_OHLCV_COLUMNS_DESC = "\n".join(
    (
        "open: daily opening price for the current stock.",
        "high: daily highest traded price for the current stock.",
        "low: daily lowest traded price for the current stock.",
        "close: daily closing price for the current stock.",
        "volume: daily traded volume for the current stock.",
    )
)
DEFAULT_OHLCV_FACTOR_DESCRIPTIONS: tuple[str, ...] = (
    "open: daily opening price.",
    "high: daily high price.",
    "low: daily low price.",
    "close: daily closing price.",
    "volume: daily traded volume.",
)
DIVERSIFIED_GUIDANCE_MODES: tuple[str, ...] = (
    "light",
    "moderate",
    "creative",
    "divergent",
    "concrete",
)

ALLOWED_IMPORT_MODULES: frozenset[str] = frozenset(
    {"math", "numpy", "pandas", "scipy", "scipy.stats", "talib"}
)
ALLOWED_ALPHA_ALIASES: frozenset[str] = frozenset(DEFAULT_ALPHA_LIBRARY_ALIASES)

FORBIDDEN_ALPHA_CALLS: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "open", "__import__"}
)
FORBIDDEN_TIME_ORDER_PATTERNS: frozenset[str] = frozenset(
    {"iloc[::-1]", "sort_index(ascending=False)"}
)


def is_ohlcv_or_factor_column(column: str) -> bool:
    """Return whether a generated Alpha Function may read this DataFrame column."""

    return column in DEFAULT_OHLCV_COLUMNS or column.startswith("factor_")
