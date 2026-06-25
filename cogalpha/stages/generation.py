"""§3.1+3.2 generation stage — 21-agent flat-parallel Diversified Guidance (STAGE-01).

Paper-faithful replacement for ``nodes/domain_agents.py``: a pure function over the
live typed :class:`~cogalpha.state.CogAlphaState` that fans out one request per
:data:`DOMAIN_AGENT_SPECS` entry (21 at paper scale; N at validation scale),
assigns each agent a guidance mode by the deterministic ``(g + i) % 5`` rotation so
*every* generation exercises all 5 Diversified-Guidance modes (cross-agent
diversity) and a fixed agent's mode rotates across generations (cross-generation
coverage), requests ``protocol.factors_per_request`` factors per agent (≥ initial
pool at paper scale), and injects the ``{effective_CoT}`` / ``{ineffective_CoT}``
adaptive feedback summaries.

Differences from the legacy node (the node→stage reshape):
- NO pydantic dict round-trip on the state — the stage receives and reads the live
  typed state and returns a ``StageResult`` (no validate/dump back to a dict).
- NO lifecycle-recording / DAG-node-envelope imports — the skill→candidate lineage
  (``agent_skill`` / ``guidance_mode``) is already attached at
  ``io.py:_build_candidate_from_function_source``; this stage emits
  ``(candidates, [InvocationRecord(skill_name, candidate_ids)])``.
- ``num_candidates`` reads ``protocol.factors_per_request`` (not the old
  ``config.alphas_per_domain_agent``); the rotation ``g`` reads ``state.generation``.

The protocol + a ``StructuredArtifactInvoker`` are injected via
:func:`make_generation_stage` (mirroring the orchestrator's DI style). Per-skill
failures are isolated (``try/except`` per agent) so one bad agent does not abort
the stage. The stage is pure / Phase-20-ready: no shared mutable singleton; the
``ordered_map`` fan-out is deterministic and order-preserving.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from cogalpha.alpha_contract import DIVERSIFIED_GUIDANCE_MODES
from cogalpha.concurrency import ordered_map
from cogalpha.protocol import ProtocolConfig
from cogalpha.schemas import AlphaCandidate, DomainAgentRequest
from cogalpha.skill_runtime.nodes import SkillNodeRuntime, StructuredArtifactInvoker
from cogalpha.skill_runtime.registry import DOMAIN_AGENT_SPECS, DomainAgentSpec
from cogalpha.stages import GenerationStage
from cogalpha.state import CogAlphaState, InvocationRecord, StageResult

__all__ = ["GenerationStageImpl", "make_generation_stage", "_guidance_mode_for"]


def _guidance_mode_for(spec_index: int, generation: int) -> str:
    """Deterministic Diversified-Guidance rotation (D-01, migrated verbatim).

    ``(generation + spec_index) % len(DIVERSIFIED_GUIDANCE_MODES)`` — every
    generation assigns all 5 modes across the agents (cross-agent diversity) and a
    fixed agent's mode rotates across generations (cross-generation coverage).
    """

    mode_index = (generation + spec_index) % len(DIVERSIFIED_GUIDANCE_MODES)
    return DIVERSIFIED_GUIDANCE_MODES[mode_index]


def _feedback_summary(feedback: object, attr: str) -> str | None:
    value = getattr(feedback, attr, None)
    return value if isinstance(value, str) else None


class GenerationStageImpl:
    """Callable ``GenerationStage`` over the live state with injected dependencies.

    ``concurrency`` is an orchestration knob only: the agent invokes are
    independent and results are collected back in agent-spec order, so
    ``concurrency=1`` reproduces the sequential behavior exactly and higher values
    do not change RESULTS (deterministic fan-out via ``ordered_map``).
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
            spec_index: int, spec: DomainAgentSpec
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
                guidance_mode=_guidance_mode_for(spec_index, generation),
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
            partial(_invoke, index, spec)
            for index, spec in enumerate(self._agent_specs)
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


def make_generation_stage(
    protocol: ProtocolConfig,
    invoker: StructuredArtifactInvoker,
    *,
    agent_specs: tuple[DomainAgentSpec, ...] = DOMAIN_AGENT_SPECS,
    concurrency: int = 1,
) -> GenerationStage:
    """Build the injected §3.1+3.2 generation stage (DI factory)."""

    return GenerationStageImpl(
        protocol,
        invoker,
        agent_specs=agent_specs,
        concurrency=concurrency,
    )
