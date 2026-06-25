"""The paper §2 deterministic outer loop — the heart of the v4.0 engine.

``run`` executes the canonical paper search loop: ``inner_subcycles`` sub-cycles
of ``subcycle_length`` generations (3 x 8 = 24 by paper default), with a
sub-cycle-boundary adaptive re-seed, per-generation §3.4 fitness partition +
top-2 elite carry, §3.5 adaptive generation every generation, §3.6 evolution,
and §4.1 injection every ``injection_every`` generations. Termination is the
**fixed generation count** ``g == protocol.generations - 1`` — never an empty
qualified pool (fixes audit wrong-design #1/#2/#5).

Every cadence bound is sourced field-by-field from
:class:`~cogalpha.protocol.ProtocolConfig`; no literal loop bound and no
``min()`` / ceiling appears on the loop path (G1; the static check is Phase 22
VAL-02). The orchestrator threads the live typed
:class:`~cogalpha.state.CogAlphaState` object (STATE-02 — no dict round-trips)
and consumes the stage / checkpoint Protocols by dependency injection
(``cogalpha/stages``), so Wave 3 plugs the paper-faithful stages in without
touching this control flow.

A partial / interrupted / budget-bounded run is a *first-class* outcome reported
via the typed :class:`RunResult` (D-10) — the orchestrator NEVER imports the old
runner completion assertion (ORCH-04). ``run`` is resume-ready: passing
``start_generation=k`` + a prior ``state`` continues deterministically from
generation ``k+1`` (D-09); the durable per-generation checkpoint file writer +
``--resume`` file I/O is CONC-06 / Phase 20.
"""

from __future__ import annotations

import dataclasses
import time
from enum import StrEnum
from typing import TYPE_CHECKING, TypedDict

from pydantic import BaseModel, ConfigDict

from cogalpha.backtest import run_portfolio_backtest
from cogalpha.schemas import AlphaCandidate
from cogalpha.skill_runtime.registry import DOMAIN_AGENT_SPECS, DomainAgentSpec
from cogalpha.stages import (
    CheckpointWriter,
    EvolutionStage,
    FitnessStage,
    GenerationStage,
    InjectionStage,
    QualityStage,
)
from cogalpha.stages.adaptive import build_adaptive_feedback
from cogalpha.stages.evolution import make_evolution_stage
from cogalpha.stages.fitness import CandidateMetricsProvider, make_fitness_stage
from cogalpha.stages.generation import make_generation_stage
from cogalpha.stages.injection import make_injection_stage
from cogalpha.stages.quality import make_quality_stage
from cogalpha.state import CogAlphaState, InvocationRecord, StageResult

if TYPE_CHECKING:
    import pandas as pd

    from cogalpha.benchmark.specs import BenchmarkSpec
    from cogalpha.execution_pool import AlphaExecPool
    from cogalpha.protocol import ProtocolConfig
    from cogalpha.skill_runtime.nodes import StructuredArtifactInvoker

__all__ = [
    "FinalizeResult",
    "NoOpCheckpointWriter",
    "RunResult",
    "RunStatus",
    "StageBundle",
    "default_stage_bundle",
    "finalize",
    "make_stage_bundle",
    "run",
]


class StageBundle(TypedDict):
    """The injected stage call-sites the orchestrator consumes (D-06/D-07).

    ``generation``/``quality``/``evolution``/``injection`` return a
    ``StageResult``; ``fitness`` is the distinct partition stage returning
    ``None``.
    """

    generation: GenerationStage
    quality: QualityStage
    evolution: EvolutionStage
    injection: InjectionStage
    fitness: FitnessStage


class RunStatus(StrEnum):
    """Outcome class for an orchestrator run (D-10). Partial is first-class."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"
    BUDGET_EXHAUSTED = "budget_exhausted"


class RunResult(BaseModel):
    """Typed run outcome (D-10). No completion-assertion crash on a partial run."""

    model_config = ConfigDict(extra="forbid")

    status: RunStatus
    generations_completed: int
    generations_target: int
    final_state: CogAlphaState
    stop_reason: str | None = None


class NoOpCheckpointWriter:
    """In-memory / no-op default checkpoint writer (D-09).

    Faithfully fills the §2 ``checkpoint(state, g)`` call-site so the loop shape
    is complete; the durable ``checkpoints/gen-<g>.json`` write + ``--resume``
    file I/O is CONC-06 / Phase 20.
    """

    def write(self, state: CogAlphaState, generation: int) -> None:
        return None


def _merge(state: CogAlphaState, result: StageResult) -> int:
    """Insert a stage's candidates into the store + candidate pool.

    ``CogAlphaState`` exposes the ``store`` dict + pool lists only (no
    ``add_candidates`` helper); merge by keying each candidate into ``store`` and
    appending its id to ``candidate_pool``. Returns the number of invocation
    records the stage reported (for the budget accounting).
    """

    candidates: list[AlphaCandidate]
    records: list[InvocationRecord]
    candidates, records = result
    for cand in candidates:
        if cand.candidate_id not in state.store:
            state.store[cand.candidate_id] = cand
            state.candidate_pool.append(cand.candidate_id)
    return len(records)


class _Budget:
    """Tracks invocation count + wall-clock against optional caps (D-11)."""

    def __init__(
        self,
        *,
        max_invocations: int | None,
        max_wall_clock: float | None,
    ) -> None:
        self._max_invocations = max_invocations
        self._max_wall_clock = max_wall_clock
        self._invocations = 0
        self._start = time.monotonic()

    def charge(self, invocations: int) -> None:
        self._invocations += invocations

    def exhausted(self) -> str | None:
        if (
            self._max_invocations is not None
            and self._invocations >= self._max_invocations
        ):
            return (
                f"max_invocations reached ({self._invocations} "
                f">= {self._max_invocations})"
            )
        if self._max_wall_clock is not None:
            elapsed = time.monotonic() - self._start
            if elapsed >= self._max_wall_clock:
                return f"max_wall_clock reached ({elapsed:.4f}s)"
        return None


def make_stage_bundle(
    protocol: ProtocolConfig,
    invoker: StructuredArtifactInvoker,
    metrics_provider: CandidateMetricsProvider,
    *,
    runtime_ohlcv_panel: pd.DataFrame | None = None,
    exec_pool: AlphaExecPool | None = None,
    require_pool: bool = False,
    agent_specs: tuple[DomainAgentSpec, ...] = DOMAIN_AGENT_SPECS,
    concurrency: int = 1,
) -> StageBundle:
    """Wire the five paper-faithful Wave-3 stages into the injected ``StageBundle``.

    The real ``make_*_stage`` factories (19-03/04/05) are injected with the shared
    ``protocol`` + ``invoker`` (and the ``metrics_provider`` for the fitness
    partition); ``runtime_ohlcv_panel`` is threaded into the quality stage so its
    A.3 stage-5 Execution & Numerical Stability guard runs against a real panel when
    supplied (``None`` keeps the static-only posture). This is the §2-loop-locked DI
    replacement of the Phase-18 honest stubs at the injection point — it does NOT
    touch the orchestrator's loop semantics.

    **``agent_specs`` cost lever (VAL-01 / Phase 22).** The domain-agent tuple is
    forwarded into BOTH the generation and injection fan-out factories so a sliced
    ``DOMAIN_AGENT_SPECS[:protocol.domain_agents]`` actually reduces the real-LLM
    invocation count to ``domain_agents`` (the biggest spend lever) instead of the
    full 21. It defaults to the full tuple, so existing offline callers are
    byte-behavior-unchanged. The slice is computed in the CALLER (``scripts/run.py``),
    not here.

    **D-01a live injection contract (20-05).** When ``require_pool`` is True this is the
    live engine run boundary: a non-None :class:`~cogalpha.execution_pool.AlphaExecPool`
    is injected into ALL THREE untrusted execution sites — the fitness ``metrics_provider``
    (``exec_pool``), the quality stage-5 Execution guard, and the leakage executed-sentinel
    (threaded into quality as ``require_pool`` -> ``require_isolation``). The contract
    asserts the pool is non-None at every site (``_assert_live_pool_contract``), so a live
    run can carry NO reachable in-process untrusted-exec branch (single isolation path, no
    fallback — 20-04's consumers are fail-closed when no pool is present; this contract
    guarantees the live path is never missing the pool in the first place).
    """

    if require_pool:
        _assert_live_pool_contract(exec_pool, metrics_provider)
        # Inject the engine-level pool into the fitness provider's untrusted-exec site
        # (the provider is a dataclass with an ``exec_pool`` field on the live path).
        metrics_provider = _inject_pool_into_provider(metrics_provider, exec_pool)

    # ``concurrency`` is a RUNTIME dispatch knob (NOT a paper-system / ProtocolConfig
    # variable, G1) forwarded into the four LLM fan-out stages' bounded ``ordered_map``
    # pool. It only parallelizes independent per-unit invokes (domain-agent generation /
    # injection, evolution plans, per-candidate quality) — results stay input-indexed, so
    # ``concurrency=1`` is byte-for-byte the serial path (default keeps offline callers
    # unchanged). The fitness stage's parallelism rides the metrics_provider's own
    # ``concurrency`` field, set where the provider is constructed.
    return {
        "generation": make_generation_stage(
            protocol, invoker, agent_specs=agent_specs, concurrency=concurrency
        ),
        "quality": make_quality_stage(
            protocol,
            invoker,
            runtime_ohlcv_panel=runtime_ohlcv_panel,
            exec_pool=exec_pool,
            require_pool=require_pool,
            concurrency=concurrency,
        ),
        "evolution": make_evolution_stage(
            protocol, invoker, concurrency=concurrency
        ),
        "injection": make_injection_stage(
            protocol, invoker, agent_specs=agent_specs, concurrency=concurrency
        ),
        "fitness": make_fitness_stage(protocol, metrics_provider),
    }


def _assert_live_pool_contract(
    exec_pool: AlphaExecPool | None,
    metrics_provider: CandidateMetricsProvider,
) -> None:
    """D-01a: assert a non-None pool is wired to all three untrusted sites.

    The three untrusted execution sites are the fitness provider, the quality stage-5
    Execution guard, and the leakage executed-sentinel. On the live run boundary
    (``require_pool=True``) the engine-level ``exec_pool`` must be non-None — quality
    stage-5 and the leakage sentinel both consume it (the latter via ``require_isolation``),
    and the fitness provider must expose an ``exec_pool`` attribute we can inject into.
    A missing pool is refused here so production never ships a reachable in-process
    untrusted-exec branch.
    """

    if exec_pool is None:
        raise ValueError(
            "live run boundary requires a non-None AlphaExecPool injected into all "
            "three untrusted sites (fitness provider / quality stage-5 / leakage "
            "executed-sentinel); refusing to wire a live bundle without a pool (D-01a)"
        )
    if not hasattr(metrics_provider, "exec_pool"):
        raise ValueError(
            "live fitness metrics_provider must expose an 'exec_pool' site for the "
            "AlphaExecPool injection (D-01a single isolation path)"
        )


def _inject_pool_into_provider(
    metrics_provider: CandidateMetricsProvider,
    exec_pool: AlphaExecPool | None,
) -> CandidateMetricsProvider:
    """Return the provider with the engine pool injected into its untrusted-exec site.

    Uses ``dataclasses.replace`` for the canonical frozen/dataclass providers
    (``PanelBackedMetricsProvider``); falls back to setting the attribute for
    light-weight providers. The pool's non-None-ness is already asserted by
    :func:`_assert_live_pool_contract`.
    """

    if dataclasses.is_dataclass(metrics_provider) and not isinstance(
        metrics_provider, type
    ):
        return dataclasses.replace(metrics_provider, exec_pool=exec_pool)
    metrics_provider.exec_pool = exec_pool  # type: ignore[attr-defined]
    return metrics_provider


def default_stage_bundle() -> StageBundle:
    """Default runnable stage bundle for the ``scripts/run.py`` dry-run (D-06).

    Delegates to ``cogalpha/stages/defaults`` which now wires the REAL Wave-3
    stages (via :func:`make_stage_bundle`) backed by a small deterministic fake
    invoker + metrics provider, so the zero-arg call stays an honest runnable
    bundle. The real-LLM invoker + market-data metrics provider are supplied in
    Phase 22; this default exercises every wired call-site without an LLM.
    """

    from cogalpha.stages.defaults import default_stage_bundle as _bundle

    return _bundle()


def run(
    protocol: ProtocolConfig,
    *,
    stages: StageBundle,
    checkpoint_writer: CheckpointWriter,
    start_generation: int = 0,
    state: CogAlphaState | None = None,
    max_invocations: int | None = None,
    max_wall_clock: float | None = None,
) -> RunResult:
    """Execute the paper §2 deterministic loop over injected stages.

    Cadence is sourced field-by-field from ``protocol``; termination is the fixed
    ``g == protocol.generations - 1`` break, never an empty pool. Returns a typed
    :class:`RunResult`; a budget hit yields ``budget_exhausted`` and a
    ``KeyboardInterrupt`` yields ``interrupted`` — both first-class partials.
    """

    generation_stage = stages["generation"]
    quality_stage = stages["quality"]
    evolution_stage = stages["evolution"]
    injection_stage = stages["injection"]
    fitness_stage = stages["fitness"]

    if state is None:
        state = CogAlphaState()

    target = protocol.generations
    # Validate the resume sentinel up front (WR-02): a negative resume point is
    # nonsensical, and a resume point beyond ``target`` (e.g. from a stale
    # checkpoint or a config that shrank ``generations`` between runs) must raise
    # rather than report a silently-wrong ``completed`` RunResult. Note
    # ``start_generation == target`` is permitted and finalizes COMPLETED below.
    if start_generation < 0:
        raise ValueError("start_generation must be >= 0")
    if start_generation > target:
        raise ValueError(
            f"start_generation ({start_generation}) exceeds "
            f"generations target ({target})"
        )
    budget = _Budget(
        max_invocations=max_invocations,
        max_wall_clock=max_wall_clock,
    )
    # ``start_generation`` is the resume sentinel: 0 means a fresh run (execute
    # from generation 0); k > 0 means generation k already completed (its
    # snapshot was the last checkpoint) so execution resumes at generation k+1
    # (D-09). ``resume_after`` is the index of the last completed generation.
    resume_after = start_generation if start_generation > 0 else -1
    generations_completed = max(start_generation, 0)
    stop_reason: str | None = None
    status = RunStatus.COMPLETED

    # A resume whose last completed generation is already the terminal generation
    # (``resume_after >= target - 1``) is COMPLETED, not PARTIAL (WR-01). The inner
    # loop would otherwise ``continue``-skip every generation (each satisfies
    # ``g <= resume_after``) and never reach the final-gen bump, leaving
    # ``generations_completed`` short of ``target`` and mis-reporting PARTIAL.
    # This also handles ``start_generation == target`` (permitted by WR-02).
    if resume_after >= target - 1:
        return RunResult(
            status=RunStatus.COMPLETED,
            generations_completed=target,
            generations_target=target,
            final_state=state,
            stop_reason=None,
        )

    def _feedback() -> object:
        # §3.5 adaptive feedback is built from the live state every generation
        # (19-03): the 2 best-qualified/elite + 2 worst-rejected candidates form
        # the {effective_CoT} / {ineffective_CoT} the generation / evolution /
        # injection stages inject. Pure: build_adaptive_feedback does not mutate
        # state. This is the loop-closing fix for the v3.0 built-but-never-looped
        # feedback defect — the loop semantics around it are unchanged.
        return build_adaptive_feedback(state)

    try:
        for subcycle in range(protocol.inner_subcycles):
            subcycle_first_g = subcycle * protocol.subcycle_length
            subcycle_last_g = subcycle_first_g + protocol.subcycle_length - 1
            # Skip sub-cycles whose every generation already completed on resume.
            if subcycle_last_g <= resume_after:
                continue

            # Sub-cycle-boundary adaptive re-seed (Reading A): the 21 task-agents
            # collectively re-initiate the search before the inner loop. Only
            # re-seed for a sub-cycle the resume has not already entered (its
            # first generation is still pending).
            if subcycle_first_g > resume_after:
                state.generation = subcycle_first_g
                budget.charge(_merge(state, generation_stage(state, _feedback())))
                _merge(state, quality_stage(state, _feedback()))

            for gen in range(protocol.subcycle_length):
                g = subcycle * protocol.subcycle_length + gen
                if g <= resume_after:
                    continue

                state.generation = g

                # §3.4 fitness partition: elite / qualified->parent / rejected.
                fitness_stage(state)
                # Top-2 elite carry is realised by the partition writing
                # parent_pool = dedup(elite_carry + qualified); the loop observes
                # it via the threaded state.

                if g == protocol.generations - 1:
                    generations_completed = g + 1
                    break

                reason = budget.exhausted()
                if reason is not None:
                    status = RunStatus.BUDGET_EXHAUSTED
                    stop_reason = reason
                    generations_completed = g
                    return RunResult(
                        status=status,
                        generations_completed=generations_completed,
                        generations_target=target,
                        final_state=state,
                        stop_reason=stop_reason,
                    )

                # §3.5 adaptive generation (every generation) + quality.
                budget.charge(_merge(state, generation_stage(state, _feedback())))
                _merge(state, quality_stage(state, _feedback()))

                # §3.6 thinking evolution on the parent pool + quality.
                budget.charge(_merge(state, evolution_stage(state, _feedback())))
                _merge(state, quality_stage(state, _feedback()))

                # §4.1 injection every ``injection_every`` generations + quality.
                if (g + 1) % protocol.injection_every == 0:
                    budget.charge(_merge(state, injection_stage(state, _feedback())))
                    _merge(state, quality_stage(state, _feedback()))

                checkpoint_writer.write(state, g)
                generations_completed = g + 1
            else:
                continue
            # inner-loop break (final-gen termination) propagates out.
            break
    except KeyboardInterrupt:
        return RunResult(
            status=RunStatus.INTERRUPTED,
            generations_completed=generations_completed,
            generations_target=target,
            final_state=state,
            stop_reason="KeyboardInterrupt",
        )

    if generations_completed < target:
        status = RunStatus.PARTIAL

    return RunResult(
        status=status,
        generations_completed=generations_completed,
        generations_target=target,
        final_state=state,
        stop_reason=stop_reason,
    )


class FinalizeResult(BaseModel):
    """Strict scalar metric surface produced by the §2 ``finalize`` seam (BACK-01/03).

    Carries the JSON-safe portfolio backtest metrics (incl.
    ``annualized_excess_return`` + ``information_ratio``, BACK-03) so plan 21-03's
    run summary reads a stable contract. ``extra="forbid"`` keeps the surface
    closed.
    """

    model_config = ConfigDict(extra="forbid")

    cumulative_return: float
    annualized_return: float
    annualized_excess_return: float
    information_ratio: float
    max_drawdown: float
    mean_turnover: float
    total_transaction_cost_return: float


def finalize(
    state: CogAlphaState,
    spec: BenchmarkSpec,
    ohlcv_panel: pd.DataFrame,
    label: pd.Series,
    *,
    combined_signal: pd.Series,
    benchmark_returns: pd.Series | None = None,
    initial_capital: float = 1_000_000.0,
    periods_per_year: int = 252,
) -> FinalizeResult:
    """§2 ``finalize`` line: final candidate pool -> combination -> backtest (BACK-01).

    This is the append-only post-run seam after the LOCKED ``run()`` loop. It is a
    pure function — it does NOT mutate ``state`` and is NOT called from inside
    ``run()``; the entry point (``scripts/run.py``, wired by plan 21-03) invokes it
    as a separate post-run step.

    Parameter contract:

    - ``state``: the ``RunResult.final_state`` whose ``elite_pool`` (a.k.a. the
      final candidate pool) is the input the combination is drawn from.
    - ``spec``: the :class:`~cogalpha.benchmark.specs.BenchmarkSpec` driving the
      top-50/drop-5 portfolio rule + cost model.
    - ``ohlcv_panel``: the ``(date,ticker)`` O-2 panel.
    - ``label``: the 10-day forward-return label, accepted (and held) here so the
      seam signature stays stable for the 21-02 combination contract.
    - ``combined_signal`` (keyword-only): a ``pd.Series`` indexed by a two-level
      ``(date,ticker)`` MultiIndex — the EXACT interface plan 21-02's combination
      trainer plugs into. 21-02 produces ``combined_signal`` from
      ``state.elite_pool`` factor values + ``label``; this seam consumes it. The
      seam itself is combination-agnostic (it takes the signal as input), so the
      two plans stay file-disjoint.

    Runs the re-homed engine-path backtest over ``combined_signal`` and returns a
    strict :class:`FinalizeResult` carrying the scalar metric surface (incl.
    AER/IR, BACK-03).
    """

    result = run_portfolio_backtest(
        combined_signal,
        ohlcv_panel,
        spec,
        benchmark_returns=benchmark_returns,
        initial_capital=initial_capital,
        periods_per_year=periods_per_year,
    )
    return FinalizeResult(**result.metrics.model_dump())
