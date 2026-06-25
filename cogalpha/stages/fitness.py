"""§3.4 fitness partition stage — DISTINCT shape, returns None (STAGE-06, D-07).

Paper-faithful replacement for ``nodes/fitness.py``: a callable over the live typed
:class:`~cogalpha.state.CogAlphaState` that scores every candidate in
``state.candidate_pool`` and partitions them into the elite / qualified->parent /
rejected pools, writing them IN PLACE and returning ``None`` (the D-07 distinct
partition shape — NEVER a ``StageResult`` with empty candidates plus side effects).

The 5-metric same-generation percentile + per-metric-minima formula
(``fitness.py:apply_fitness_gate``) is KEPT verbatim; this stage only assembles the
``FitnessGateConfig`` it consumes, feeding the benchmark-selected qualified+elite minima
pair from ``ProtocolConfig.per_metric_minima`` (keyed by ``benchmark_spec.preset_id``).
A CSI300 config uses MI>=0.02, an S&P500 config uses MI>=0.012 (App A.4; all other
metrics identical); an unmapped benchmark id falls back to the CSI300 pair (A2).

The node->stage reshape (vs. ``nodes/fitness.py``):
- NO pydantic state validate/dump dict round-trip, NO ``DAGNodeResult`` envelope, NO
  ``build_generation_feedback`` (19-03 adaptive owns the per-gen feedback now).
- the lifecycle helpers (``classify_candidates_by_fitness`` / ``select_parent_pool`` /
  ``dedupe_candidates`` / ``record_fitness_decision`` / ``candidate_metrics``) are
  migrated INLINE here so 19-06 can delete ``candidates/lifecycle.py``.
- the pools are id-ref lists over ``state.store`` (G1 bounded memory); the accumulated
  elite pool is deduped each generation and the parent pool respects
  ``protocol.elite_carry`` + ``protocol.parent_pool``.

Pure / Phase-20-ready: no shared mutable singleton; deterministic over the typed state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from cogalpha.config import FitnessGateConfig
from cogalpha.fitness import apply_fitness_gate, composite_fitness_score
from cogalpha.protocol import ProtocolConfig
from cogalpha.schemas import (
    AlphaCandidate,
    CandidateStage,
    FitnessDecision,
    FitnessMetrics,
)
from cogalpha.stages import FitnessStage
from cogalpha.state import CogAlphaState

__all__ = ["CandidateMetricsProvider", "FitnessStageImpl", "make_fitness_stage"]

# CSI300 is the paper's primary benchmark and the documented A2 fallback when the
# active benchmark id is not present in ``per_metric_minima``.
_FALLBACK_BENCHMARK_ID = "cogalpha_csi300_ohlcv_v1"


class CandidateMetricsProvider(Protocol):
    """Evaluates alpha candidates against market data (injected dependency)."""

    def evaluate(
        self, candidates: Sequence[AlphaCandidate]
    ) -> Mapping[str, FitnessMetrics]:
        """Return fitness metrics keyed by candidate id."""


def _gate_config(protocol: ProtocolConfig) -> FitnessGateConfig:
    """Assemble the KEPT ``FitnessGateConfig`` from the protocol + benchmark minima.

    The percentiles are the paper-pinned 65/80; the qualified+elite minima are the
    benchmark-selected pair from ``per_metric_minima`` (App A.4 dataset-configurable),
    falling back to the CSI300 pair for an unmapped benchmark id (A2 assumption).
    """

    benchmark_id = protocol.benchmark_spec.preset_id
    minima = protocol.per_metric_minima.get(benchmark_id)
    if minima is None:
        minima = protocol.per_metric_minima.get(_FALLBACK_BENCHMARK_ID)
    if minima is None:  # pragma: no cover - defensive: defaults always seed CSI300
        return FitnessGateConfig(
            qualified_percentile=protocol.qualified_percentile,
            elite_percentile=protocol.elite_percentile,
        )
    return FitnessGateConfig(
        qualified_percentile=protocol.qualified_percentile,
        elite_percentile=protocol.elite_percentile,
        qualified_minima=minima.qualified,
        elite_minima=minima.elite,
    )


def _candidate_metrics(candidate: AlphaCandidate) -> FitnessMetrics | None:
    """Return the fitness metrics stored on a candidate, when available.

    Migrated inline from ``candidates/lifecycle.py:candidate_metrics`` so the elite
    carry-forward ordering can re-read metrics off the carried elites. The metrics
    are stored as the live :class:`FitnessMetrics` object (no dict round-trip); the
    isinstance guard tolerates a candidate that never carried a metrics object.
    """

    raw_metrics = candidate.metadata.get("fitness_metrics")
    if isinstance(raw_metrics, FitnessMetrics):
        return raw_metrics
    return None


def _record_fitness_decision(
    candidate: AlphaCandidate, decision: FitnessDecision | None
) -> AlphaCandidate:
    """Attach one fitness decision to a candidate (migrated inline; no mutation).

    The metrics are stored as the live :class:`FitnessMetrics` object — no dict
    serialization round-trip — which the 19-03 adaptive reader still accepts
    unchanged (pydantic validation accepts a same-type model instance).
    """

    updated = candidate.model_copy(deep=True)
    if decision is None:
        updated.stage = CandidateStage.REJECTED_BY_FITNESS
        return updated
    updated.stage = decision.stage
    updated.metadata["fitness_metrics"] = decision.metrics
    return updated


def _dedupe(candidates: Sequence[AlphaCandidate]) -> list[AlphaCandidate]:
    """Deduplicate candidates by id, preserving last write (migrated inline)."""

    deduped: dict[str, AlphaCandidate] = {}
    for candidate in candidates:
        deduped[candidate.candidate_id] = candidate
    return list(deduped.values())


def _select_parent_pool(
    *,
    qualified: Sequence[AlphaCandidate],
    existing_elites: Sequence[AlphaCandidate],
    parent_pool_size: int,
    elite_carry_forward: int,
) -> list[AlphaCandidate]:
    """Select the parent pool from carried elites + newly qualified (migrated inline).

    The top ``elite_carry_forward`` elites (ranked by composite fitness) are carried
    forward, deduped with the newly qualified candidates, and bounded to
    ``parent_pool_size``.
    """

    elite_carry = sorted(
        existing_elites,
        key=lambda candidate: composite_fitness_score(_candidate_metrics(candidate)),
        reverse=True,
    )[:elite_carry_forward]
    return _dedupe(list(elite_carry) + list(qualified))[:parent_pool_size]


class FitnessStageImpl:
    """Callable ``FitnessStage`` — partitions the candidate pool, returns None."""

    def __init__(
        self,
        protocol: ProtocolConfig,
        metrics_provider: CandidateMetricsProvider,
    ) -> None:
        self._protocol = protocol
        self._metrics_provider = metrics_provider

    def __call__(self, state: CogAlphaState) -> None:
        candidates = [
            state.store[candidate_id]
            for candidate_id in state.candidate_pool
            if candidate_id in state.store
        ]
        metrics_by_id = dict(self._metrics_provider.evaluate(candidates))
        decisions = apply_fitness_gate(metrics_by_id, _gate_config(self._protocol))
        decision_by_id = {decision.candidate_id: decision for decision in decisions}

        new_elite: list[AlphaCandidate] = []
        qualified: list[AlphaCandidate] = []
        rejected: list[AlphaCandidate] = []
        for candidate in candidates:
            decision = decision_by_id.get(candidate.candidate_id)
            updated = _record_fitness_decision(candidate, decision)
            state.store[candidate.candidate_id] = updated
            if decision is None:
                rejected.append(updated)
            elif decision.stage == CandidateStage.ELITE:
                new_elite.append(updated)
                qualified.append(updated)
            elif decision.stage == CandidateStage.QUALIFIED:
                qualified.append(updated)
            else:
                rejected.append(updated)

        prior_elites = [
            state.store[candidate_id]
            for candidate_id in state.elite_pool
            if candidate_id in state.store
        ]
        elites = _dedupe(prior_elites + new_elite)
        parents = _select_parent_pool(
            qualified=qualified,
            existing_elites=elites,
            parent_pool_size=self._protocol.parent_pool,
            elite_carry_forward=self._protocol.elite_carry,
        )

        # Write the pools in place as id-ref lists (G1 bounded memory).
        state.elite_pool = [candidate.candidate_id for candidate in elites]
        state.qualified_pool = [candidate.candidate_id for candidate in qualified]
        state.parent_pool = [candidate.candidate_id for candidate in parents]
        state.rejected_pool.extend(candidate.candidate_id for candidate in rejected)
        state.candidate_pool = []
        return None


def make_fitness_stage(
    protocol: ProtocolConfig,
    metrics_provider: CandidateMetricsProvider,
) -> FitnessStage:
    """Build the injected §3.4 fitness partition stage (DI factory; returns None)."""

    return FitnessStageImpl(protocol, metrics_provider)
