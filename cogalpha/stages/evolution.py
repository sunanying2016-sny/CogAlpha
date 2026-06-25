"""§3.6 thinking-evolution stage — 3 ops, children fixed to children_pool (STAGE-04).

Paper-faithful replacement for ``nodes/evolution.py`` + ``candidates/evolution.py``: a
callable over the live typed :class:`~cogalpha.state.CogAlphaState` that reads the parent
pool (ids -> store) and produces EXACTLY ``protocol.children_pool`` children (= 3 x
parent_pool; 96 at paper scale) across the 3 paper operations — mutation, crossover, and
crossover->mutation. This FIXES the legacy label-only defect where the child count was
parent-derived (``len(mutations) + len(crossovers) + ...``) and no stage ever read
``children_pool``.

Determinism (paradigm constraint, no RNG): the operations are emitted in a fixed
round-robin (``mutation`` -> ``crossover`` -> ``crossover_then_mutation``) over the
parent pool (parents and adjacent pairs cycle with wrap-around), so the same parent pool
+ generation always yields the same plan. The crossover->mutation follow-up stays
sequential within its step (the mutation depends on the crossover child). Generation
stops as soon as ``children_pool`` children exist; the children list is truncated to the
exact target.

The node->stage reshape (vs. the migration sources):
- NO pydantic state validate/dump dict round-trip, NO ``record_evolution_child`` /
  ``DAGNodeResult`` envelope — the stage reads the live state and returns a
  ``StageResult`` ``(children, [InvocationRecord, ...])``.
- ``EvolutionSkillPlan`` + ``select_evolution_parents`` + ``adjacent_parent_pairs`` are
  migrated INLINE here so 19-06 can delete ``candidates/evolution.py``.
- the child count reads ``protocol.children_pool`` (the fix), not a parent-derived sum.

The quality re-entry (each child re-enters the checker) is performed by the orchestrator
loop (``evolution_stage`` -> ``quality_stage``), NOT inside this stage. Per-skill failures
are isolated; the stage is pure / Phase-20-ready (no shared mutable singleton).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from cogalpha.protocol import ProtocolConfig
from cogalpha.schemas import (
    AlphaCandidate,
    EvolutionOperation,
    EvolutionSkillRequest,
)
from cogalpha.skill_runtime.nodes import SkillNodeRuntime, StructuredArtifactInvoker
from cogalpha.stages import EvolutionStage
from cogalpha.state import CogAlphaState, InvocationRecord, StageResult

__all__ = ["EvolutionStageImpl", "make_evolution_stage"]

MUTATION_SKILL = "alpha-mutation"
CROSSOVER_SKILL = "alpha-crossover"

# Fixed deterministic operation cycle (no RNG): one round produces a mutation, a
# crossover, and a crossover->mutation pair so all 3 ops appear early.
_OPERATION_CYCLE: tuple[EvolutionOperation, ...] = (
    EvolutionOperation.MUTATION,
    EvolutionOperation.CROSSOVER,
    EvolutionOperation.CROSSOVER_THEN_MUTATION,
)


@dataclass(frozen=True)
class _EvolutionPlan:
    """One evolution operation invocation and its lineage intent (migrated inline)."""

    skill_name: str
    operation: EvolutionOperation
    parents: tuple[AlphaCandidate, ...]
    generation: int
    lineage_parent_ids: tuple[str, ...]

    def to_request(
        self,
        *,
        effective_feedback_summary: str | None,
        ineffective_feedback_summary: str | None,
    ) -> EvolutionSkillRequest:
        return EvolutionSkillRequest(
            operation=self.operation,
            parents=list(self.parents),
            generation=self.generation,
            effective_feedback_summary=effective_feedback_summary,
            ineffective_feedback_summary=ineffective_feedback_summary,
        )


def _select_parents(parent_pool: Sequence[AlphaCandidate], size: int) -> list[AlphaCandidate]:
    """Choose the bounded parent slice used by evolution (migrated inline)."""

    return list(parent_pool[:size])


def _adjacent_pairs(
    parents: Sequence[AlphaCandidate],
) -> list[tuple[AlphaCandidate, AlphaCandidate]]:
    """Pair adjacent parents for crossover (migrated inline)."""

    return list(zip(parents[0::2], parents[1::2], strict=False))


def _feedback_summary(feedback: object, attr: str) -> str | None:
    value = getattr(feedback, attr, None)
    return value if isinstance(value, str) else None


@dataclass
class _Children:
    """Accumulator that stops exactly at the target child count."""

    target: int
    items: list[AlphaCandidate] = field(default_factory=list)

    @property
    def full(self) -> bool:
        return len(self.items) >= self.target

    def add(self, child: AlphaCandidate | None) -> None:
        if child is not None and not self.full:
            self.items.append(child)


class EvolutionStageImpl:
    """Callable ``EvolutionStage`` producing exactly ``children_pool`` children.

    ``concurrency`` is accepted for DI parity but the plan is built sequentially so
    the deterministic round-robin + the crossover->mutation dependency are preserved
    exactly (no result reordering); the value does not change RESULTS.
    """

    def __init__(
        self,
        protocol: ProtocolConfig,
        invoker: StructuredArtifactInvoker,
        *,
        concurrency: int = 1,
    ) -> None:
        self._protocol = protocol
        self._runtime = SkillNodeRuntime(invoker)
        self._concurrency = concurrency

    def __call__(self, state: CogAlphaState, feedback: object) -> StageResult:
        parents = [
            state.store[candidate_id]
            for candidate_id in state.parent_pool
            if candidate_id in state.store
        ]
        target = self._protocol.children_pool
        children = _Children(target=target)
        records: list[InvocationRecord] = []
        if not parents:
            return [], []

        effective = _feedback_summary(feedback, "effective_feedback_summary")
        ineffective = _feedback_summary(feedback, "ineffective_feedback_summary")
        generation = state.generation + 1
        pairs = _adjacent_pairs(parents) or [(parents[0], parents[0])]

        mutation_i = 0
        crossover_i = 0
        op_i = 0
        # Deterministic round-robin until the exact target is met. A stall guard
        # bounds the loop so a persistently-failing invoker (every ``_invoke``
        # returns None → no child appended) terminates with a partial (possibly
        # empty) result instead of spinning forever (CR-02). The guard NEVER fires
        # on the happy path: any successful child resets ``stalled`` to 0, so when
        # invokes succeed the loop behaves byte-identically to before.
        stall_limit = 2 * (len(parents) + len(pairs)) + 4
        stalled = 0
        while not children.full:
            before = len(children.items)
            operation = _OPERATION_CYCLE[op_i % len(_OPERATION_CYCLE)]
            op_i += 1
            if operation == EvolutionOperation.MUTATION:
                parent = parents[mutation_i % len(parents)]
                mutation_i += 1
                self._run_mutation(
                    parent, generation, effective, ineffective, children, records
                )
            else:  # CROSSOVER or CROSSOVER_THEN_MUTATION both start with a crossover
                left, right = pairs[crossover_i % len(pairs)]
                crossover_i += 1
                self._run_crossover(
                    left,
                    right,
                    generation,
                    effective,
                    ineffective,
                    children,
                    records,
                    then_mutate=operation == EvolutionOperation.CROSSOVER_THEN_MUTATION,
                )
            if len(children.items) == before:
                stalled += 1
                if stalled >= stall_limit:
                    break
            else:
                stalled = 0

        return children.items[:target], records

    def _run_mutation(
        self,
        parent: AlphaCandidate,
        generation: int,
        effective: str | None,
        ineffective: str | None,
        children: _Children,
        records: list[InvocationRecord],
    ) -> None:
        plan = _EvolutionPlan(
            skill_name=MUTATION_SKILL,
            operation=EvolutionOperation.MUTATION,
            parents=(parent,),
            generation=generation,
            lineage_parent_ids=(parent.candidate_id,),
        )
        child = self._invoke(plan, effective, ineffective, records)
        children.add(child)

    def _run_crossover(
        self,
        left: AlphaCandidate,
        right: AlphaCandidate,
        generation: int,
        effective: str | None,
        ineffective: str | None,
        children: _Children,
        records: list[InvocationRecord],
        *,
        then_mutate: bool,
    ) -> None:
        crossover_plan = _EvolutionPlan(
            skill_name=CROSSOVER_SKILL,
            operation=EvolutionOperation.CROSSOVER,
            parents=(left, right),
            generation=generation,
            lineage_parent_ids=(left.candidate_id, right.candidate_id),
        )
        crossover_child = self._invoke(crossover_plan, effective, ineffective, records)
        children.add(crossover_child)
        if not then_mutate or crossover_child is None or children.full:
            return
        # Crossover->mutation: mutate the crossover child, preserving original parents.
        mutation_plan = _EvolutionPlan(
            skill_name=MUTATION_SKILL,
            operation=EvolutionOperation.CROSSOVER_THEN_MUTATION,
            parents=(crossover_child,),
            generation=generation,
            lineage_parent_ids=(left.candidate_id, right.candidate_id),
        )
        mutated = self._invoke(mutation_plan, effective, ineffective, records)
        children.add(mutated)

    def _invoke(
        self,
        plan: _EvolutionPlan,
        effective: str | None,
        ineffective: str | None,
        records: list[InvocationRecord],
    ) -> AlphaCandidate | None:
        request = plan.to_request(
            effective_feedback_summary=effective,
            ineffective_feedback_summary=ineffective,
        )
        try:
            child = self._runtime.alpha_candidate(plan.skill_name, request)
        except Exception:  # noqa: BLE001 - per-skill isolation (one bad op != abort)
            return None
        child = self._attach_lineage(child, plan)
        records.append(
            InvocationRecord(skill_name=plan.skill_name, candidate_ids=[child.candidate_id])
        )
        return child

    @staticmethod
    def _attach_lineage(child: AlphaCandidate, plan: _EvolutionPlan) -> AlphaCandidate:
        updated = child.model_copy(deep=True)
        updated.lineage.operation = updated.lineage.operation or plan.operation
        if not updated.lineage.parent_ids:
            updated.lineage.parent_ids = list(plan.lineage_parent_ids)
        updated.lineage.generation = plan.generation
        updated.lineage.agent_skill = updated.lineage.agent_skill or plan.skill_name
        return updated


def make_evolution_stage(
    protocol: ProtocolConfig,
    invoker: StructuredArtifactInvoker,
    *,
    concurrency: int = 1,
) -> EvolutionStage:
    """Build the injected §3.6 evolution stage (DI factory; children == children_pool)."""

    return EvolutionStageImpl(protocol, invoker, concurrency=concurrency)
