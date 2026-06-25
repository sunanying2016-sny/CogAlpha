"""Engine-path run I/O: run-dir/JSON writers, LLM env helpers, run summary (BACK-04).

Consolidated out of the deleted ``cogalpha/runners/common.py`` island so the run
manifest / summary / report writers live on the engine import path (ROADMAP
Phase 21 success criterion #3). The five run-I/O / LLM-env symbols are moved
VERBATIM (byte-identical bodies; the T-21-SEC redaction posture is unchanged):
``resolve_run_dir`` (path-traversal-safe), ``write_json`` (stable sorted JSON),
``configure_llm_provider``, ``load_key_file``, ``_canonical_llm_env_name``, plus
the five ``DEFAULT_*`` LLM-env consts.

``summarize_cogalpha_run`` is REWRITTEN against the LIVE runtime types
(``state.CogAlphaState`` + ``orchestrator.RunResult``); it no longer touches the
dead legacy-state pseudo-history / per-call cache-hit / per-call error reads that
the live runtime does not produce (RESEARCH Pitfall 3). It aggregates the
per-generation fitness evidence (Phase-20 stateless ``CandidateEvaluationResult``
lists), the 21-02 combined signal (shape/coverage only), and the 21-01 backtest
AER/IR + IC-family.

Security (T-21-SEC-01/03): the summary records ONLY run/metric/signal/backtest
data — never an env-var value, never an API key, never a key-file line. Every
value placed in the payload is plain str/int/float/bool/list/dict, so no object
can smuggle env contents into the serialized artifact. No new logging of
``os.environ`` is added anywhere in this module.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

    from cogalpha import schemas
    from cogalpha.orchestrator import RunResult

__all__ = [
    "DEFAULT_DEEPSEEK_BASE_URL",
    "DEFAULT_DEEPSEEK_MODEL",
    "DEFAULT_DEEPSEEK_REASONING_EFFORT",
    "DEFAULT_DEEPSEEK_THINKING",
    "DEFAULT_OPENAI_CHAT_MODEL",
    "configure_llm_provider",
    "load_key_file",
    "resolve_run_dir",
    "summarize_cogalpha_run",
    "write_json",
]

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_REASONING_EFFORT = "max"
DEFAULT_DEEPSEEK_THINKING = "enabled"
DEFAULT_OPENAI_CHAT_MODEL = "gpt-4o-mini-2024-07-18"


def load_key_file(path: str | Path) -> None:
    """Load local key-file aliases into process environment without overriding explicit env."""

    key_path = Path(path)
    if not key_path.exists():
        return
    parsed: dict[str, str] = {}
    for raw_line in key_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+)", line)
        if not match:
            continue
        name, value = match.groups()
        parsed[name] = value.strip().strip('"').strip("'")

    for name, value in parsed.items():
        canonical_name = _canonical_llm_env_name(name)
        if canonical_name:
            os.environ.setdefault(canonical_name, value)
        elif name.isupper():
            os.environ.setdefault(name, value)


def configure_llm_provider(args: Any) -> None:
    """Apply provider defaults and CLI overrides to CogAlpha LLM environment variables."""

    if args.provider == "deepseek":
        os.environ.setdefault("COGALPHA_LLM_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL)
        os.environ.setdefault("COGALPHA_LLM_MODEL", DEFAULT_DEEPSEEK_MODEL)
        os.environ.setdefault("COGALPHA_LLM_REASONING_EFFORT", DEFAULT_DEEPSEEK_REASONING_EFFORT)
        os.environ.setdefault("COGALPHA_LLM_THINKING", DEFAULT_DEEPSEEK_THINKING)
        if args.max_tokens is not None:
            os.environ.setdefault("COGALPHA_LLM_MAX_TOKENS", str(args.max_tokens))
    elif args.provider == "openai":
        os.environ.setdefault("COGALPHA_LLM_MODEL", DEFAULT_OPENAI_CHAT_MODEL)

    if args.model:
        os.environ["COGALPHA_LLM_MODEL"] = args.model
    if args.base_url:
        os.environ["COGALPHA_LLM_BASE_URL"] = args.base_url
    if args.reasoning_effort:
        os.environ["COGALPHA_LLM_REASONING_EFFORT"] = args.reasoning_effort
    if args.thinking:
        os.environ["COGALPHA_LLM_THINKING"] = args.thinking
    if args.max_tokens is not None:
        os.environ["COGALPHA_LLM_MAX_TOKENS"] = str(args.max_tokens)


def resolve_run_dir(output_root: str | Path, run_id: str) -> Path:
    """Resolve a run artifact directory without allowing run_id path traversal."""

    root = Path(output_root).resolve()
    run_id_path = Path(run_id)
    if (
        run_id_path.is_absolute()
        or ".." in run_id_path.parts
        or len(run_id_path.parts) != 1
    ):
        raise ValueError("run_id must be a single relative path component")

    run_dir = (root / run_id).resolve()
    if not run_dir.is_relative_to(root):
        raise ValueError("run_id escapes output_root")
    return run_dir


def write_json(path: str | Path, payload: Any) -> None:
    """Write stable JSON used by run artifacts."""

    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _canonical_llm_env_name(name: str) -> str | None:
    normalized = name.lower().replace("-", "_")
    if normalized in {
        "key",
        "api_key",
        "llm_api_key",
        "deepseek_api_key",
        "openai_api_key",
    }:
        return "COGALPHA_LLM_API_KEY"
    if normalized in {"model", "llm_model", "chat_model", "deepseek_model", "openai_model"}:
        return "COGALPHA_LLM_MODEL"
    if normalized in {
        "base_url",
        "api_base",
        "llm_base_url",
        "deepseek_base_url",
        "openai_base_url",
    }:
        return "COGALPHA_LLM_BASE_URL"
    if normalized in {"reasoning_effort", "deepseek_reasoning_effort"}:
        return "COGALPHA_LLM_REASONING_EFFORT"
    if normalized in {"thinking", "deepseek_thinking"}:
        return "COGALPHA_LLM_THINKING"
    if normalized in {"max_tokens", "llm_max_tokens", "deepseek_max_tokens"}:
        return "COGALPHA_LLM_MAX_TOKENS"
    return None


# ---------------------------------------------------------------------------
# Run summary (BACK-04) — rewritten against the LIVE runtime types.
# ---------------------------------------------------------------------------

_FITNESS_FIELDS = ("ic", "rank_ic", "icir", "rank_icir", "mi")


def _aggregate_generation(
    results: list[schemas.CandidateEvaluationResult],
) -> dict[str, Any]:
    """Aggregate one generation's scored fitness metrics into a bounded summary.

    Reads each result's ``.metrics`` (``FitnessMetrics``) and reduces them to a
    per-field count / mean / min / max — NOT the per-candidate raw vectors (keeps
    the JSON bounded). Results without metrics (guard-rejected / errored) are
    skipped from the scored aggregation but counted via ``total``.
    """

    scored: list[dict[str, float]] = [
        result.metrics.model_dump()
        for result in results
        if result.metrics is not None
    ]
    block: dict[str, Any] = {
        "total": int(len(results)),
        "scored_count": int(len(scored)),
    }
    if not scored:
        block["mean"] = {}
        block["min"] = {}
        block["max"] = {}
        return block

    mean: dict[str, float] = {}
    minimum: dict[str, float] = {}
    maximum: dict[str, float] = {}
    for field_name in _FITNESS_FIELDS:
        values = [float(row[field_name]) for row in scored]
        mean[field_name] = float(sum(values) / len(values))
        minimum[field_name] = float(min(values))
        maximum[field_name] = float(max(values))
    block["mean"] = mean
    block["min"] = minimum
    block["max"] = maximum
    return block


def _combined_signal_block(combined_signal: pd.Series | None) -> dict[str, Any] | None:
    """Describe the 21-02 combined signal by shape/coverage (NOT the raw floats).

    Records length, date span, date count, and ticker count off the
    ``(date,ticker)`` two-level MultiIndex. Every value is JSON-serializable
    (ints + ISO date strings); the float series itself is never embedded.
    """

    if combined_signal is None:
        return None

    index = combined_signal.index
    dates = index.get_level_values("date")
    tickers = index.get_level_values("ticker")
    unique_dates = sorted({_iso_date(d) for d in dates.unique()})
    block: dict[str, Any] = {
        "length": int(len(combined_signal)),
        "date_count": int(len(unique_dates)),
        "ticker_count": int(tickers.nunique()),
    }
    if unique_dates:
        block["date_span"] = {"start": unique_dates[0], "end": unique_dates[-1]}
    else:
        block["date_span"] = None
    return block


def _iso_date(value: Any) -> str:
    """Return an ISO date string for a pandas timestamp / date-like label."""

    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _backtest_block(
    backtest_metrics: dict[str, Any] | None,
    ic_family: schemas.FitnessMetrics | None,
) -> dict[str, Any] | None:
    """Build the backtest block: 21-01 AER/IR scalar surface + IC-family.

    ``backtest_metrics`` is the JSON-safe scalar metric mapping from the 21-01
    backtest (``FinalizeResult.model_dump()`` or
    ``PortfolioBacktestResult.metrics.model_dump()``); it carries
    ``annualized_excess_return`` / ``information_ratio`` (BACK-03). ``ic_family``
    is the IC/RankIC/ICIR/RankICIR/MI surfaced during the run/finalize. Returns
    ``None`` when no backtest ran (e.g. the offline-bundle dry-run with no panel).
    """

    if backtest_metrics is None and ic_family is None:
        return None

    block: dict[str, Any] = {}
    if backtest_metrics is not None:
        for key, value in backtest_metrics.items():
            block[str(key)] = _json_scalar(value)
    if ic_family is not None:
        block["ic_family"] = {
            field_name: float(getattr(ic_family, field_name))
            for field_name in _FITNESS_FIELDS
        }
    return block


def _json_scalar(value: Any) -> Any:
    """Coerce a scalar to a JSON-safe primitive (no pandas/numpy objects)."""

    if isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    # numpy scalar / other numeric-like: fall back to float, else str.
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def summarize_cogalpha_run(
    run_result: RunResult,
    per_gen_results: list[list[schemas.CandidateEvaluationResult]],
    *,
    combined_signal: pd.Series | None = None,
    backtest_metrics: dict[str, Any] | None = None,
    ic_family: schemas.FitnessMetrics | None = None,
) -> dict[str, Any]:
    """Return the JSON-serializable run summary against the LIVE runtime types.

    Aggregates four sections (RESEARCH Pitfall 3 — none read the dead legacy
    state; the legacy pseudo-history / per-call cache-hit / per-call error keys
    are dropped entirely):

    - ``run``: status / generations completed vs target / stop_reason / final
      pool sizes, read off ``RunResult`` + its ``final_state`` id-pools.
    - ``per_generation_fitness``: a per-generation bounded aggregation of the
      Phase-20 stateless ``CandidateEvaluationResult.metrics`` (count + per-field
      mean/min/max). An empty input (e.g. the offline dry-run, which collects no
      per-gen evidence) yields an empty list — the ``run`` block is still
      populated.
    - ``combined_signal``: the 21-02 signal's shape/coverage (length / date span
      / ticker count), or ``None`` when no signal was produced.
    - ``backtest``: the 21-01 AER/IR scalar surface + the IC-family, or ``None``
      when no backtest ran (offline bundle has no panel).

    Security (T-21-SEC-01): the payload contains ONLY run/metric/signal/backtest
    data. No env-var value, API key, or key-file line is ever placed here; every
    value is a plain primitive.
    """

    state = run_result.final_state
    summary: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "run": {
            "status": str(run_result.status),
            "generations_completed": int(run_result.generations_completed),
            "generations_target": int(run_result.generations_target),
            "stop_reason": run_result.stop_reason,
            "final_generation": int(state.generation),
            "final_pool_sizes": {
                "elite": int(len(state.elite_pool)),
                "qualified": int(len(state.qualified_pool)),
                "rejected": int(len(state.rejected_pool)),
                "candidate": int(len(state.candidate_pool)),
                "parent": int(len(state.parent_pool)),
                "store": int(len(state.store)),
            },
        },
        "per_generation_fitness": [
            {"generation": gen_index, **_aggregate_generation(results)}
            for gen_index, results in enumerate(per_gen_results)
        ],
        "combined_signal": _combined_signal_block(combined_signal),
        "backtest": _backtest_block(backtest_metrics, ic_family),
    }
    return summary
