"""Default runnable stage bundle for the §2 orchestrator dry-run (D-06).

Wave 3 replaced the Phase-18 honest stubs with the REAL paper-faithful stages
(``make_generation_stage`` / ``make_quality_stage`` / ``make_evolution_stage`` /
``make_injection_stage`` / ``make_fitness_stage`` — 19-03/04/05) wired via
``orchestrator.make_stage_bundle``. To keep the zero-argument
``default_stage_bundle()`` an honest runnable bundle for ``scripts/run.py`` without
an LLM or market data, this module supplies a small DETERMINISTIC fake invoker and
a deterministic metrics provider:

- ``_FakeInvoker`` answers every skill call by output schema: domain/injection
  agents get a leak-clean trailing candidate batch, evolution gets a single
  leak-clean child, and quality always ACCEPTs (the real A.3 ordering still runs).
- ``_DeterministicMetricsProvider`` scores each candidate with fixed monotone
  metrics so the fitness partition populates the elite / qualified / parent pools
  and the §3.5 adaptive feedback has scored candidates to summarize every gen.

These are honest interface implementations, NOT shims over deleted code. The
real-LLM ``StructuredArtifactInvoker`` + a market-data ``CandidateMetricsProvider``
are supplied in Phase 22; this default exercises every wired call-site offline.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, TypeVar, cast

from pydantic import BaseModel

from cogalpha.protocol import ProtocolConfig
from cogalpha.schemas import (
    AlphaCandidate,
    AlphaCandidateBatch,
    AlphaFunction,
    FitnessMetrics,
    QualityDecision,
    QualityVerdict,
    SkillKind,
    SkillRef,
)

if TYPE_CHECKING:
    from cogalpha.orchestrator import StageBundle

__all__ = ["default_stage_bundle"]

_SchemaT = TypeVar("_SchemaT", bound=BaseModel)

# A leak-clean trailing factor (only uses past values via shift(1)) so the wired
# A.3 temporal-leakage gate (static scan + executed sentinel) passes every gen.
_CLEAN_CODE = (
    "def {name}(df):\n"
    "    df_copy = df.copy()\n"
    "    df_copy['{name}'] = df_copy['close'].shift(1)\n"
    "    return df_copy['{name}']\n"
)


def _clean_candidate(candidate_id: str) -> AlphaCandidate:
    name = f"factor_{candidate_id}".replace("-", "_")
    return AlphaCandidate(
        candidate_id=candidate_id,
        alpha=AlphaFunction(
            name=name,
            code=_CLEAN_CODE.format(name=name),
            formula="close.shift(1)",
            rationale="deterministic fake-invoker candidate (offline dry-run).",
        ),
    )


class _FakeInvoker:
    """Deterministic offline invoker answering by requested output schema.

    Uniqueness: each candidate id mixes the skill name + a per-skill call counter
    so repeated invocations across generations produce distinct candidate ids (the
    loop's ``_merge`` therefore keeps adding fresh candidates rather than dropping
    duplicates).
    """

    def __init__(self) -> None:
        self._counter = 0

    def invoke(
        self,
        skill_name: str,
        request: BaseModel,
        output_schema: type[_SchemaT],
    ) -> _SchemaT:
        self._counter += 1
        tag = f"{skill_name}-{self._counter}"
        artifact: BaseModel
        if output_schema is QualityDecision:
            artifact = QualityDecision(
                skill=SkillRef(
                    name=skill_name,
                    path=f"skills/{skill_name}/SKILL.md",
                    kind=SkillKind.QUALITY_CHECKER,
                ),
                verdict=QualityVerdict.ACCEPT,
                practical_soundness="offline dry-run soundness",
                feedback="offline dry-run feedback",
            )
        elif output_schema is AlphaCandidate:
            artifact = _clean_candidate(f"evo-{tag}")
        else:
            # Domain / injection agents: a small leak-clean candidate batch.
            num = getattr(request, "num_candidates", 1)
            candidates = [_clean_candidate(f"{tag}-{k}") for k in range(num)]
            artifact = AlphaCandidateBatch(candidates=candidates)
        return cast("_SchemaT", artifact)


class _DeterministicMetricsProvider:
    """Score each candidate with fixed monotone metrics keyed off its id.

    The score is a stable hash of the candidate id mapped into ``[0.2, 0.95]`` so
    the fitness partition gets a deterministic spread (some elite, some qualified,
    some rejected) without RNG and without market data.
    """

    def evaluate(
        self, candidates: Sequence[AlphaCandidate]
    ) -> Mapping[str, FitnessMetrics]:
        metrics: dict[str, FitnessMetrics] = {}
        for candidate in candidates:
            digest = hashlib.sha256(candidate.candidate_id.encode()).digest()
            score = 0.2 + (digest[0] / 255.0) * 0.75
            metrics[candidate.candidate_id] = FitnessMetrics(
                ic=score,
                rank_ic=score,
                icir=score,
                rank_icir=score,
                mi=score,
            )
        return metrics


def default_stage_bundle() -> StageBundle:
    """Return the runnable default bundle wiring the REAL Wave-3 stages (D-06).

    Uses ``ProtocolConfig.validation()`` for the wired factories' protocol-driven
    bounds (the orchestrator is driven by whatever protocol ``run`` receives; the
    stages only read protocol fields like ``factors_per_request`` /
    ``quality_repair_attempts`` / ``children_pool`` / the minima — all benchmark
    defaults), backed by the deterministic offline invoker + metrics provider.
    """

    from cogalpha.orchestrator import make_stage_bundle

    return make_stage_bundle(
        ProtocolConfig.validation(),
        _FakeInvoker(),
        _DeterministicMetricsProvider(),
    )
