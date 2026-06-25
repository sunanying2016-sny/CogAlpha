"""§4.1 injection stage — task-agent alphas, quality-filtered via the loop (STAGE-05).

NEW stage (no 1:1 migration source): a callable over the live typed
:class:`~cogalpha.state.CogAlphaState` that produces task-agent alpha candidates by the
same flat-parallel fan-out as generation — one request per task-agent spec, each asking
for ``protocol.factors_per_request`` factors — and returns them as a ``StageResult``
``(candidates, [InvocationRecord, ...])``.

Boundaries (locked by the §2 orchestrator loop, NOT this stage):
- **Cadence:** the loop fires injection only every other generation (the every-2-gens
  gate, sourced from the protocol cadence field). This stage does NOT decide WHEN to
  run — it only runs when called. There is no cadence / modulo check here.
- **Quality filter:** the loop calls ``quality_stage`` immediately after injection, so
  the injected alphas are quality-filtered before they reach the parent pool. This stage
  does NOT call quality itself.
- **Pool merge:** the loop's ``_merge`` inserts the returned candidates into the store +
  candidate pool. This stage is PURE — it does not mutate any state pool directly.

The fan-out pattern is re-implemented here directly over ``concurrency.ordered_map``
(NOT imported from ``stages/generation.py`` — 19-05 declares no same-wave dependency on
19-03; generation is a pattern analog only). Per-skill failures are isolated so one bad
task-agent does not abort the stage; the fan-out is deterministic and order-preserving
(Phase-20-ready: no shared mutable singleton).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from cogalpha.concurrency import ordered_map
from cogalpha.protocol import ProtocolConfig
from cogalpha.schemas import AlphaCandidate, DomainAgentRequest
from cogalpha.skill_runtime.nodes import SkillNodeRuntime, StructuredArtifactInvoker
from cogalpha.skill_runtime.registry import DOMAIN_AGENT_SPECS, DomainAgentSpec
from cogalpha.stages import InjectionStage
from cogalpha.state import CogAlphaState, InvocationRecord, StageResult

__all__ = ["InjectionStageImpl", "make_injection_stage"]


def _feedback_summary(feedback: object, attr: str) -> str | None:
    value = getattr(feedback, attr, None)
    return value if isinstance(value, str) else None


class InjectionStageImpl:
    """Callable ``InjectionStage`` fanning task-agent alphas out via ``ordered_map``.

    ``concurrency`` is an orchestration knob only: the task-agent invokes are
    independent and results are collected back in spec order, so ``concurrency=1``
    reproduces the sequential behavior exactly and higher values do not change RESULTS.
    """

    def __init__(
        self,
        protocol: ProtocolConfig,
        invoker: StructuredArtifactInvoker,
        *,
        agent_specs: tuple[DomainAgentSpec, ...] = DOMAIN_AGENT_SPECS,
        concurrency: int = 1,
    ) -> None:
        self._protocol = protocol
        self._runtime = SkillNodeRuntime(invoker)
        self._agent_specs = agent_specs
        self._concurrency = concurrency

    def __call__(self, state: CogAlphaState, feedback: object) -> StageResult:
        effective = _feedback_summary(feedback, "effective_feedback_summary")
        ineffective = _feedback_summary(feedback, "ineffective_feedback_summary")
        generation = state.generation
        num_candidates = self._protocol.factors_per_request

        def _invoke(
            spec: DomainAgentSpec,
        ) -> tuple[list[AlphaCandidate], InvocationRecord] | None:
            request = DomainAgentRequest(
                skill_name=spec.skill_name,
                paper_agent_name=spec.paper_name,
                level=spec.level,
                layer=spec.layer,
                focus=spec.focus,
                factor_type=spec.focus,
                num_candidates=num_candidates,
                generation=generation,
                effective_feedback_summary=effective,
                ineffective_feedback_summary=ineffective,
            )
            try:
                batch = self._runtime.candidate_batch(spec.skill_name, request)
            except Exception:  # noqa: BLE001 - per-skill isolation (one bad agent != abort)
                return None
            produced = list(batch.candidates)
            record = InvocationRecord(
                skill_name=spec.skill_name,
                candidate_ids=[candidate.candidate_id for candidate in produced],
            )
            return produced, record

        tasks: list[Callable[[], tuple[list[AlphaCandidate], InvocationRecord] | None]] = [
            partial(_invoke, spec) for spec in self._agent_specs
        ]
        outcomes = ordered_map(tasks, concurrency=self._concurrency)

        candidates: list[AlphaCandidate] = []
        records: list[InvocationRecord] = []
        for outcome in outcomes:
            if outcome is None:
                continue
            produced, record = outcome
            candidates.extend(produced)
            records.append(record)
        return candidates, records


def make_injection_stage(
    protocol: ProtocolConfig,
    invoker: StructuredArtifactInvoker,
    *,
    agent_specs: tuple[DomainAgentSpec, ...] = DOMAIN_AGENT_SPECS,
    concurrency: int = 1,
) -> InjectionStage:
    """Build the injected §4.1 injection stage (DI factory; pure / Phase-20-ready)."""

    return InjectionStageImpl(
        protocol,
        invoker,
        agent_specs=agent_specs,
        concurrency=concurrency,
    )
