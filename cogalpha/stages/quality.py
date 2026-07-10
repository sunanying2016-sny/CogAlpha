"""§3.3 multi-agent quality checker — verbatim App A.3 order + leakage gate (STAGE-02).

Paper-faithful replacement for ``nodes/quality_pipeline.py``: a pure callable over the
live typed :class:`~cogalpha.state.CogAlphaState` that runs each candidate in
``state.candidate_pool`` through the verbatim App A.3 ordered sequence:

  1. **Code Quality** (LLM verdict)
  2. **Code Repair** (LLM, retry up to ``protocol.quality_repair_attempts`` then
     discard — D-02)
  3. **Judge** (LLM verdict)
  4. **Logic Improvement** (LLM, on a Judge REPAIR)
  5. **Execution & Numerical Stability** (``run_runtime_alpha_code_guard`` — the
     deterministic sandbox; the A.3 stage-5 gate)
  6. **Temporal Leakage** (``temporal_leakage_stage`` — the NET-NEW hard-reject gate;
     the A.3 stage-6 / v4.0 honesty backbone)

All LLM verdicts arrive as the single structured :class:`QualityVerdict` (the JSON
schema parsed in 19-02 ``skill_runtime/verdict.py`` via ``io.py``); branching is over
the enum, never free text. A candidate REJECTED at any step — or unfixable after the
configured repair attempts, or leaking — is routed to ``state.rejected_pool`` and is
NEVER returned as accepted. A clean candidate that clears all six steps is returned in
the ``StageResult`` accepted list.

The node→stage reshape (vs. ``nodes/quality_pipeline.py``):
- NO pydantic state validate/dump dict round-trip, NO DAG-node-result envelope, NO
  deprecated dict-state — the stage reads the live ``state.py`` state + the canonical
  ``AlphaCandidate``.
- the repair-loop bound reads ``protocol.quality_repair_attempts`` (the D-02 knob), not
  the legacy MVP-loop repair-count config field.
- the deterministic guards are NAMED A.3 steps 5 & 6 (Execution & Numerical Stability,
  then Temporal Leakage) — not a guards-first pre-pass.

Pure / Phase-20-ready: no shared mutable singleton; per-candidate failures are isolated
(``try/except``) so one bad LLM artifact does not abort the stage; the ``ordered_map``
fan-out is deterministic and order-preserving.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

import pandas as pd

from cogalpha.concurrency import ordered_map
from cogalpha.evaluation import run_alpha_code_via_pool
from cogalpha.guards.alpha_runtime import (
    build_runtime_execution_failure_report,
    build_runtime_numeric_stability_report,
    run_runtime_alpha_code_guard,
)
from cogalpha.protocol import ProtocolConfig
from cogalpha.schemas import (
    AlphaCandidate,
    CandidateStage,
    GuardReport,
    GuardStatus,
    QualityDecision,
    QualitySkillRequest,
    QualityVerdict,
)
from cogalpha.skill_runtime.nodes import SkillNodeRuntime, StructuredArtifactInvoker
from cogalpha.stages import QualityStage
from cogalpha.stages.leakage import temporal_leakage_stage
from cogalpha.state import CogAlphaState, InvocationRecord, StageResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cogalpha.execution_pool import AlphaExecPool

__all__ = ["QualityStageImpl", "make_quality_stage"]


@dataclass(frozen=True)
class _CandidateOutcome:
    """One candidate's A.3 result: accepted XOR rejected, plus the skills invoked."""

    accepted: AlphaCandidate | None
    rejected: AlphaCandidate | None
    invoked_skills: list[str]


def _with_stage(candidate: AlphaCandidate, stage: CandidateStage) -> AlphaCandidate:
    """Return a copy of the candidate tagged with a quality lifecycle stage.

    Migrated inline from ``candidates/lifecycle.py`` (deleted in 19-06): the three
    quality-record helpers below are the only lifecycle helpers this stage used, so
    they live here now (mirroring how 19-05 migrated the fitness lifecycle helpers
    inline into ``stages/fitness.py``). No mutation of the original artifact.
    """

    updated = candidate.model_copy(deep=True)
    updated.stage = stage
    return updated


def record_repair(candidate: AlphaCandidate) -> AlphaCandidate:
    """Mark a repaired Alpha Candidate without mutating the original (migrated inline)."""

    return _with_stage(candidate, CandidateStage.REPAIRED)


def record_quality_acceptance(candidate: AlphaCandidate) -> AlphaCandidate:
    """Mark an Alpha Candidate accepted by the Quality checker (migrated inline)."""

    return _with_stage(candidate, CandidateStage.ACCEPTED_BY_QUALITY)


def record_quality_rejection(candidate: AlphaCandidate) -> AlphaCandidate:
    """Mark an Alpha Candidate rejected by the Quality checker (migrated inline)."""

    return _with_stage(candidate, CandidateStage.REJECTED_BY_QUALITY)


class QualityStageImpl:
    """Callable ``QualityStage`` running the A.3 ordered checker over the live state.

    ``runtime_ohlcv_panel`` is the OHLCV sample the A.3 stage-5 Execution & Numerical
    Stability guard executes the factor against; when ``None`` that step is skipped
    (static-only posture, mirroring ``DeterministicGuardPipeline``). ``concurrency`` is
    an orchestration knob only — per-candidate checks are independent and collected
    back in pool order, so ``concurrency=1`` reproduces the sequential behavior exactly.
    """

    def __init__(
        self,
        protocol: ProtocolConfig,
        invoker: StructuredArtifactInvoker,
        *,
        runtime_ohlcv_panel: pd.DataFrame | None = None,
        max_nan_fraction: float = 0.30,
        concurrency: int = 1,
        exec_pool: AlphaExecPool | None = None,
        require_pool: bool = False,
        pool_timeout_seconds: float = 30.0,
    ) -> None:
        self._protocol = protocol
        self._runtime = SkillNodeRuntime(invoker)
        self._panel = runtime_ohlcv_panel
        self._max_nan_fraction = max_nan_fraction
        self._concurrency = concurrency
        # D-01a/D-01b: untrusted stage-5 / leakage-sentinel execution routes through
        # the injected shared pool. ``require_pool`` marks the live engine path where
        # an absent/dead pool must fail CLOSED (never in-process exec). When
        # ``require_pool`` is False (standalone unit-test path) the in-process guard is
        # used as the stage's own trusted harness. ``exec_pool=None`` is fixture-only.
        self._exec_pool = exec_pool
        self._require_pool = require_pool
        self._pool_timeout_seconds = pool_timeout_seconds

    def __call__(self, state: CogAlphaState, feedback: object) -> StageResult:
        candidates = [
            state.store[candidate_id]
            for candidate_id in state.candidate_pool
            if candidate_id in state.store
        ]

        tasks: list[Callable[[], _CandidateOutcome]] = [
            partial(self._process_candidate, candidate) for candidate in candidates
        ]
        outcomes = ordered_map(tasks, concurrency=self._concurrency)

        accepted: list[AlphaCandidate] = []
        rejected: list[AlphaCandidate] = []
        records: list[InvocationRecord] = []
        for candidate, outcome in zip(candidates, outcomes, strict=True):
            for skill_name in outcome.invoked_skills:
                records.append(
                    InvocationRecord(skill_name=skill_name, candidate_ids=[candidate.candidate_id])
                )
            if outcome.accepted is not None:
                accepted.append(outcome.accepted)
            else:
                rejected.append(outcome.rejected or candidate)

        # The stage owns its filtering side-effect (CR-01): quality is a real FILTER,
        # so it both routes rejects to ``rejected_pool`` AND prunes them out of
        # ``candidate_pool`` — leaving ONLY survivors for the following fitness stage.
        # The orchestrator's ``_merge`` is purely additive (skips same-id stores), so
        # without this prune the rejected/leaking ids would stay in ``candidate_pool``
        # and fitness would score the unfiltered pool (the §3.3/§3.6 honesty bypass).
        # We also write the quality-tagged (ACCEPTED_BY_QUALITY / REJECTED_BY_QUALITY)
        # copy back into the store for every candidate, since ``_merge`` would otherwise
        # drop that lifecycle tag (the id already lives in the store from generation) and
        # checkpoints would show the stale pre-quality ``stage`` for every reject.
        rejected_ids = [candidate.candidate_id for candidate in rejected]
        rejected_set = set(rejected_ids)
        state.candidate_pool = [
            candidate_id
            for candidate_id in state.candidate_pool
            if candidate_id not in rejected_set
        ]
        for candidate in accepted:
            state.store[candidate.candidate_id] = candidate
        for candidate in rejected:
            state.store[candidate.candidate_id] = candidate
        state.rejected_pool.extend(rejected_ids)
        return accepted, records

    def _process_candidate(self, candidate: AlphaCandidate) -> _CandidateOutcome:
        invoked: list[str] = []
        try:
            return self._run_a3_sequence(candidate, invoked)
        except Exception:  # noqa: BLE001 - isolate one bad LLM artifact (Phase-20-ready)
            return _CandidateOutcome(
                accepted=None,
                rejected=record_quality_rejection(candidate),
                invoked_skills=invoked,
            )

    def _run_a3_sequence(
        self, candidate: AlphaCandidate, invoked: list[str]
    ) -> _CandidateOutcome:
        current = candidate

        # --- Step 1: Code Quality (LLM verdict) --------------------------------
        decision = self._invoke("alpha-code-quality", current, invoked)
        if decision.verdict == QualityVerdict.REJECT:
            return self._reject(candidate, invoked)
        if decision.verdict == QualityVerdict.REPAIR:
            repaired = self._attempt_code_repair(current, invoked)  # Step 2
            if repaired is None:
                return self._reject(candidate, invoked)
            current = repaired

        # --- Step 3: Judge (LLM verdict) ---------------------------------------
        judge = self._invoke("alpha-judge", current, invoked)
        if judge.verdict == QualityVerdict.REJECT:
            return self._reject(candidate, invoked)
        if judge.verdict == QualityVerdict.REPAIR:
            improved = self._attempt_logic_improvement(current, invoked)  # Step 4
            if improved is None:
                return self._reject(candidate, invoked)
            current = improved

        # --- Step 5: Execution & Numerical Stability (deterministic guard) -----
        if self._panel is not None:
            runtime_report = self._run_stage5_guard(current)
            if runtime_report.status == GuardStatus.FAIL:
                return self._reject(candidate, invoked)

        # --- Step 6: Temporal Leakage (NET-NEW, hard reject) -------------------
        leak, _reasons = temporal_leakage_stage(
            current.alpha,
            exec_pool=self._exec_pool,
            require_isolation=self._require_pool,
        )
        if leak:
            return self._reject(candidate, invoked)

        return _CandidateOutcome(
            accepted=record_quality_acceptance(current),
            rejected=None,
            invoked_skills=invoked,
        )

    def _run_stage5_guard(self, candidate: AlphaCandidate) -> GuardReport:
        """A.3 stage-5 Execution & Numerical Stability, untrusted exec via the pool.

        Live path (``require_pool`` True OR a pool is injected): the untrusted code
        runs in the shared ``AlphaExecPool`` (D-01b) and the numeric-stability
        disposition is built from the isolated result. An absent/dead pool on the
        live path fails CLOSED → guard FAIL, never in-process exec (D-01a). The
        standalone unit-test path (no pool, ``require_pool`` False) uses the
        in-process guard as the stage's own trusted harness.
        """

        assert self._panel is not None  # caller guards this
        if self._exec_pool is None and not self._require_pool:
            # Standalone trusted harness path (unit tests): in-process guard.
            return run_runtime_alpha_code_guard(
                candidate, self._panel, max_nan_fraction=self._max_nan_fraction
            )

        # Live path: route untrusted execution through the shared isolation pool.
        [exec_result] = run_alpha_code_via_pool(
            self._exec_pool, [candidate], timeout_seconds=self._pool_timeout_seconds
        )
        if not exec_result.ok or exec_result.factor_values is None:
            return build_runtime_execution_failure_report(
                candidate_id=candidate.candidate_id,
                message=exec_result.error or "untrusted execution failed (fail-closed)",
                max_nan_fraction=self._max_nan_fraction,
            )
        return build_runtime_numeric_stability_report(
            candidate_id=candidate.candidate_id,
            factor_series=exec_result.factor_values,
            max_nan_fraction=self._max_nan_fraction,
        )

    def _attempt_code_repair(
        self, candidate: AlphaCandidate, invoked: list[str]
    ) -> AlphaCandidate | None:
        """Code Repair retry bounded by ``protocol.quality_repair_attempts`` (D-02).

        Returns the repaired candidate, or ``None`` when unfixable after the
        configured attempts (or a hard REJECT) — the caller then discards it to
        ``rejected_pool``.
        """

        for _attempt in range(self._protocol.quality_repair_attempts):
            decision = self._invoke("alpha-code-repair", candidate, invoked)
            if decision.repaired_candidate is not None:
                return record_repair(decision.repaired_candidate)
            if decision.verdict == QualityVerdict.REJECT:
                return None
        return None

    def _attempt_logic_improvement(
        self, candidate: AlphaCandidate, invoked: list[str]
    ) -> AlphaCandidate | None:
        decision = self._invoke("alpha-logic-improvement", candidate, invoked)
        if decision.repaired_candidate is not None:
            return record_repair(decision.repaired_candidate)
        return None

    def _invoke(
        self, skill_name: str, candidate: AlphaCandidate, invoked: list[str]
    ) -> QualityDecision:
        invoked.append(skill_name)
        request = QualitySkillRequest(candidate=candidate)
        return self._runtime.quality_decision(skill_name, request)

    @staticmethod
    def _reject(candidate: AlphaCandidate, invoked: list[str]) -> _CandidateOutcome:
        # ``invoked`` carries the skills run before rejection so their InvocationRecords
        # are still emitted (the candidate→skill linkage is not lost on a reject).
        tagged = record_quality_rejection(candidate)
        return _CandidateOutcome(
            accepted=None, rejected=tagged, invoked_skills=list(invoked)
        )


def make_quality_stage(
    protocol: ProtocolConfig,
    invoker: StructuredArtifactInvoker,
    *,
    runtime_ohlcv_panel: pd.DataFrame | None = None,
    max_nan_fraction: float = 0.30,
    concurrency: int = 1,
    exec_pool: AlphaExecPool | None = None,
    require_pool: bool = False,
    pool_timeout_seconds: float = 30.0,
) -> QualityStage:
    """Build the injected §3.3 A.3 quality stage (DI factory; no shared singleton)."""

    return QualityStageImpl(
        protocol,
        invoker,
        runtime_ohlcv_panel=runtime_ohlcv_panel,
        max_nan_fraction=max_nan_fraction,
        concurrency=concurrency,
        exec_pool=exec_pool,
        require_pool=require_pool,
        pool_timeout_seconds=pool_timeout_seconds,
    )
