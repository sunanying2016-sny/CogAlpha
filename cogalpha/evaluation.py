"""Panel-backed candidate evaluation for the Fitness Gate."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from cogalpha.concurrency import ordered_map, resolve_max_workers
from cogalpha.data import MarketDataSplit

# ``execute_alpha_candidate`` is intentionally NOT used on the engine path (D-01a:
# untrusted code runs ONLY in the subprocess pool). It is imported here solely as the
# fail-closed tripwire target the tests patch to prove no in-process exec ever runs.
from cogalpha.execution import execute_alpha_candidate  # noqa: F401
from cogalpha.execution_pool import AlphaExecResult
from cogalpha.fitness import compute_predictive_metrics
from cogalpha.guards.alpha_runtime import (
    build_runtime_execution_failure_report,
    build_runtime_numeric_stability_report,
)
from cogalpha.schemas import (
    AlphaCandidate,
    CandidateEvaluationResult,
    FitnessMetrics,
    GuardReport,
    GuardStatus,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cogalpha.execution_pool import AlphaExecPool

# The pool-routed untrusted execution's per-candidate timeout (seconds). The pool
# is the single timeout authority (CONC-01); this is the fitness-site default and is
# overridable per provider — there is no concurrency cap literal here (G1).
_DEFAULT_POOL_TIMEOUT_SECONDS = 30.0


def run_alpha_code_via_pool(
    pool: AlphaExecPool | None,
    candidates: Sequence[AlphaCandidate],
    *,
    timeout_seconds: float,
) -> list[AlphaExecResult]:
    """Execute untrusted alpha code through the injected pool (the SOLE live path).

    This is the single shared entrypoint the three untrusted execution sites
    (fitness-eval / quality stage-5 / leakage executed-sentinel) route through so
    that stages never build their own pool (STATE-02 purity) and D-01b "one pool
    shared by all sites" holds.

    **Fail-closed (D-01a):** when ``pool`` is ``None`` (a fixture-only seam, or a
    dead pool on the live path) every candidate is dispositioned an execution
    failure (``AlphaExecResult(ok=False)``). Untrusted code is NEVER run in-process
    here — the subprocess pool is the only live execution path.
    """

    items = list(candidates)
    if pool is None:
        return [
            AlphaExecResult(
                candidate_id=candidate.candidate_id,
                ok=False,
                error=(
                    "AlphaExecPool unavailable (fail-closed): untrusted alpha code is "
                    "not executed in-process (D-01a)."
                ),
            )
            for candidate in items
        ]
    return pool.evaluate_alpha_code(items, timeout_seconds=timeout_seconds)


@dataclass(frozen=True)
class EvaluationCacheRecord:
    """One public evaluation cache record."""

    cache_key: str
    candidate_id: str
    alpha_fingerprint: str
    data_version: str
    split_name: str | None
    metrics: FitnessMetrics | None
    guard_report: GuardReport | None
    error: str | None = None


@dataclass
class EvaluationCache:
    """JSONL cache for deterministic candidate evaluation artifacts.

    The on-disk JSONL format is unchanged (backward compatible). At runtime the
    cache builds an in-memory index once (last-wins for duplicate keys, matching
    the historical ``reversed(load_all())`` lookup) and serves ``get`` as an O(1)
    dict lookup. ``put`` is a lock-guarded dual-write (memory index + file append)
    with no TOCTOU duplicate under concurrency.
    """

    path: Path | str
    _index: dict[str, EvaluationCacheRecord] = field(
        init=False, repr=False, default_factory=dict
    )
    _lock: threading.Lock = field(init=False, repr=False, default_factory=threading.Lock)
    _loaded: bool = field(init=False, repr=False, default=False)

    def _ensure_loaded(self) -> None:
        """Build the in-memory index once (double-checked, last-wins)."""

        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            for record in self.load_all():
                # Later records overwrite earlier ones => last-wins semantics.
                self._index[record.cache_key] = record
            self._loaded = True

    def get(
        self,
        candidate: AlphaCandidate,
        *,
        data_version: str,
        split_name: str | None = None,
        max_nan_fraction: float,
    ) -> EvaluationCacheRecord | None:
        """Return the cached record for one candidate/evaluation setting, if present."""

        cache_key = build_evaluation_cache_key(
            candidate,
            data_version=data_version,
            split_name=split_name,
            max_nan_fraction=max_nan_fraction,
        )
        self._ensure_loaded()
        with self._lock:
            return self._index.get(cache_key)

    def put(self, record: EvaluationCacheRecord) -> None:
        """Persist a cache record unless this key is already present."""

        self._ensure_loaded()
        with self._lock:
            if record.cache_key in self._index:
                return
            cache_path = Path(self.path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_record_to_json(record), sort_keys=True) + "\n")
            self._index[record.cache_key] = record

    def load_all(self) -> list[EvaluationCacheRecord]:
        """Load all cache records in insertion order."""

        cache_path = Path(self.path)
        if not cache_path.exists():
            return []
        records: list[EvaluationCacheRecord] = []
        for raw_line in cache_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            records.append(_record_from_json(json.loads(raw_line)))
        return records


def build_evaluation_cache_key(
    candidate: AlphaCandidate,
    *,
    data_version: str,
    split_name: str | None = None,
    max_nan_fraction: float,
) -> str:
    """Return a stable key for deterministic candidate evaluation."""

    payload = {
        "candidate_id": candidate.candidate_id,
        "alpha_fingerprint": alpha_fingerprint(candidate),
        "data_version": data_version,
        "split_name": split_name,
        "max_nan_fraction": max_nan_fraction,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def alpha_fingerprint(candidate: AlphaCandidate) -> str:
    """Return a stable fingerprint for an Alpha Candidate's executable contract."""

    payload = {
        "alpha": candidate.alpha.model_dump(mode="json"),
        "lineage": candidate.lineage.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass
class PanelBackedMetricsProvider:
    """Evaluate Alpha Candidates against OHLCV Input and forward returns.

    Untrusted alpha code is executed through the injected ``exec_pool``
    (:class:`~cogalpha.execution_pool.AlphaExecPool`) -- subprocess isolation +
    per-task timeout->SIGKILL (CONC-01). The trusted numpy metric math runs over a
    bounded ``ordered_map`` thread pool sized by ``concurrency`` (input-indexed
    determinism: ``concurrency=1`` is byte-equal to the serial path).

    **D-01a:** ``exec_pool=None`` is a fixture-only seam. On the live engine path the
    pool is always injected (20-05). When the pool is absent/dead the provider fails
    CLOSED -- the uncached untrusted candidate is dispositioned an execution failure
    (``rejected_pool``); untrusted code is NEVER run in-process.
    """

    ohlcv_panel: pd.DataFrame
    forward_returns: pd.DataFrame
    data_version: str = "unversioned"
    split_name: str | None = None
    max_nan_fraction: float = 0.30
    cache: EvaluationCache | None = None
    exec_pool: AlphaExecPool | None = None
    concurrency: int = 1
    pool_timeout_seconds: float = _DEFAULT_POOL_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        # G1: normalize the single bounded concurrency knob -- no hardcoded cap.
        self.concurrency = resolve_max_workers(self.concurrency)

    @classmethod
    def from_split(
        cls,
        data_split: MarketDataSplit,
        *,
        data_version: str = "unversioned",
        max_nan_fraction: float = 0.30,
        cache: EvaluationCache | None = None,
        exec_pool: AlphaExecPool | None = None,
        concurrency: int = 1,
        pool_timeout_seconds: float = _DEFAULT_POOL_TIMEOUT_SECONDS,
    ) -> PanelBackedMetricsProvider:
        """Build a metrics provider from one prepared market-data split."""

        return cls(
            ohlcv_panel=data_split.ohlcv_panel,
            forward_returns=data_split.forward_returns,
            data_version=data_version,
            split_name=data_split.name,
            max_nan_fraction=max_nan_fraction,
            cache=cache,
            exec_pool=exec_pool,
            concurrency=concurrency,
            pool_timeout_seconds=pool_timeout_seconds,
        )

    def evaluate(self, candidates: Sequence[AlphaCandidate]) -> Mapping[str, FitnessMetrics]:
        """Return five-metric fitness scores for candidates that pass runtime guards."""

        return {
            result.candidate_id: result.metrics
            for result in self.evaluate_candidates(candidates)
            if result.metrics is not None
        }

    def evaluate_candidates(
        self,
        candidates: Sequence[AlphaCandidate],
    ) -> list[CandidateEvaluationResult]:
        """Return structured evaluation results for every candidate.

        Untrusted code for the cache-missed candidates is executed in one batch
        through the injected pool (process isolation); the trusted numpy metric math
        for the guard-passing candidates runs in input-indexed order over a bounded
        ``ordered_map`` thread pool. Cache hits skip the pool entirely (20-03). A
        pool timeout / worker death / absent pool is dispositioned an execution
        failure per candidate -- it never aborts the batch and never falls back to
        in-process exec (D-01a).
        """

        items = list(candidates)
        results: list[CandidateEvaluationResult | None] = [None] * len(items)

        uncached: list[tuple[int, AlphaCandidate]] = []
        for index, candidate in enumerate(items):
            cached = self._cached_record(candidate)
            if cached is not None:
                results[index] = CandidateEvaluationResult(
                    candidate_id=candidate.candidate_id,
                    metrics=cached.metrics,
                    guard_report=cached.guard_report,
                    error=cached.error,
                    cache_hit=True,
                    data_version=cached.data_version,
                )
            else:
                uncached.append((index, candidate))

        if uncached:
            self._evaluate_uncached(uncached, results)

        return [result for result in results if result is not None]

    def _evaluate_uncached(
        self,
        uncached: list[tuple[int, AlphaCandidate]],
        results: list[CandidateEvaluationResult | None],
    ) -> None:
        """Pool-execute untrusted code, then parallelize the trusted metric math."""

        candidates = [candidate for _index, candidate in uncached]
        # (a)+(b): untrusted guard execution + factor-series production -> the pool
        # (process isolation + SIGKILL). Fail-closed when the pool is absent/dead.
        exec_results = run_alpha_code_via_pool(
            self.exec_pool,
            candidates,
            timeout_seconds=self.pool_timeout_seconds,
        )
        exec_by_id = {result.candidate_id: result for result in exec_results}

        # Build the per-candidate guard disposition from the isolated execution, then
        # schedule the TRUSTED metric computation for the guard-passing factor series.
        metric_tasks: list[tuple[int, AlphaCandidate, GuardReport]] = []
        for index, candidate in uncached:
            exec_result = exec_by_id.get(candidate.candidate_id)
            if exec_result is None or not exec_result.ok or exec_result.factor_values is None:
                error = (
                    exec_result.error
                    if exec_result is not None and exec_result.error
                    else "untrusted execution failed (fail-closed)"
                )
                guard_report = build_runtime_execution_failure_report(
                    candidate_id=candidate.candidate_id,
                    message=error,
                    max_nan_fraction=self.max_nan_fraction,
                )
                self._cache_record(
                    candidate, metrics=None, guard_report=guard_report, error=error
                )
                results[index] = CandidateEvaluationResult(
                    candidate_id=candidate.candidate_id,
                    guard_report=guard_report,
                    error=error,
                    cache_hit=False,
                    data_version=self.data_version,
                )
                continue

            guard_report = build_runtime_numeric_stability_report(
                candidate_id=candidate.candidate_id,
                factor_series=exec_result.factor_values,
                max_nan_fraction=self.max_nan_fraction,
            )
            if guard_report.status == GuardStatus.FAIL:
                self._cache_record(candidate, metrics=None, guard_report=guard_report)
                results[index] = CandidateEvaluationResult(
                    candidate_id=candidate.candidate_id,
                    guard_report=guard_report,
                    error="runtime guard failed",
                    cache_hit=False,
                    data_version=self.data_version,
                )
                continue

            metric_tasks.append((index, candidate, guard_report))

        if not metric_tasks:
            return

        # (c): trusted numpy metric math over a bounded thread pool (input-indexed,
        # deterministic). concurrency=1 reproduces the serial path byte-for-byte.
        functions = [
            partial(
                compute_predictive_metrics,
                exec_by_id[candidate.candidate_id].factor_values,
                self.forward_returns,
            )
            for _index, candidate, _guard in metric_tasks
        ]
        computed = ordered_map(functions, concurrency=self.concurrency)

        for (index, candidate, guard_report), metrics in zip(
            metric_tasks, computed, strict=True
        ):
            self._cache_record(candidate, metrics=metrics, guard_report=guard_report)
            results[index] = CandidateEvaluationResult(
                candidate_id=candidate.candidate_id,
                metrics=metrics,
                guard_report=guard_report,
                cache_hit=False,
                data_version=self.data_version,
            )

    def _cached_record(self, candidate: AlphaCandidate) -> EvaluationCacheRecord | None:
        if self.cache is None:
            return None
        return self.cache.get(
            candidate,
            data_version=self.data_version,
            split_name=self.split_name,
            max_nan_fraction=self.max_nan_fraction,
        )

    def _cache_record(
        self,
        candidate: AlphaCandidate,
        *,
        metrics: FitnessMetrics | None,
        guard_report: GuardReport | None,
        error: str | None = None,
    ) -> None:
        if self.cache is None:
            return
        record = EvaluationCacheRecord(
            cache_key=build_evaluation_cache_key(
                candidate,
                data_version=self.data_version,
                split_name=self.split_name,
                max_nan_fraction=self.max_nan_fraction,
            ),
            candidate_id=candidate.candidate_id,
            alpha_fingerprint=alpha_fingerprint(candidate),
            data_version=self.data_version,
            split_name=self.split_name,
            metrics=metrics,
            guard_report=guard_report,
            error=error,
        )
        self.cache.put(record)


def _record_to_json(record: EvaluationCacheRecord) -> dict[str, Any]:
    return {
        "cache_key": record.cache_key,
        "candidate_id": record.candidate_id,
        "alpha_fingerprint": record.alpha_fingerprint,
        "data_version": record.data_version,
        "split_name": record.split_name,
        "metrics": record.metrics.model_dump(mode="json") if record.metrics is not None else None,
        "guard_report": (
            record.guard_report.model_dump(mode="json")
            if record.guard_report is not None
            else None
        ),
        "error": record.error,
    }


def _record_from_json(raw: dict[str, Any]) -> EvaluationCacheRecord:
    metrics = raw.get("metrics")
    guard_report = raw.get("guard_report")
    return EvaluationCacheRecord(
        cache_key=raw["cache_key"],
        candidate_id=raw["candidate_id"],
        alpha_fingerprint=raw["alpha_fingerprint"],
        data_version=raw["data_version"],
        split_name=raw.get("split_name"),
        metrics=FitnessMetrics.model_validate(metrics) if metrics is not None else None,
        guard_report=GuardReport.model_validate(guard_report) if guard_report is not None else None,
        error=raw.get("error"),
    )
