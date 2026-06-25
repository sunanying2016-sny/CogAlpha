"""§3.5 adaptive feedback stage — wired into every generation (STAGE-03).

Builds the ``{effective_CoT}`` / ``{ineffective_CoT}`` adaptive samples the
generation stage injects, from the LIVE typed :class:`~cogalpha.state.CogAlphaState`
pools: the 2 best-qualified/elite (effective) + the 2 worst-rejected (ineffective)
candidates of the current generation. This is the closed loop that makes the search
*improve* across generations rather than restart — fixing the v3.0
built-but-never-looped feedback defect.

The ``build_generation_feedback`` core is migrated VERBATIM here from the legacy
``candidates/feedback.py`` (which 19-06 deletes); ``build_adaptive_feedback`` is the
pure-function wrapper that reconstructs the ``FitnessDecision`` sequence from the
live state pools (each candidate's ``fitness_metrics`` metadata + its
``CandidateStage``) WITHOUT any ``model_validate`` / ``model_dump`` dict round-trip,
and WITHOUT mutating the state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from cogalpha.fitness import composite_fitness_score
from cogalpha.schemas import (
    AlphaCandidate,
    CandidateStage,
    FeedbackPolarity,
    FeedbackSample,
    FitnessDecision,
    FitnessMetrics,
    GenerationFeedback,
)
from cogalpha.state import CogAlphaState

__all__ = ["build_adaptive_feedback", "build_generation_feedback"]

EFFECTIVE_STAGES = {CandidateStage.QUALIFIED, CandidateStage.ELITE}


def build_adaptive_feedback(state: CogAlphaState) -> GenerationFeedback:
    """Build the adaptive generation feedback from the live state pools.

    Reads the qualified / elite / rejected pools (id-refs over ``state.store``),
    reconstructs a :class:`FitnessDecision` per scored candidate from its
    ``fitness_metrics`` metadata + its persisted ``CandidateStage``, and delegates
    to :func:`build_generation_feedback` with ``effective_sample_size=2`` /
    ``ineffective_sample_size=2``. Pure: it does not mutate ``state`` and performs
    no dict round-trip. Candidates lacking ``fitness_metrics`` (unscored) are
    skipped — gen-0 / empty states therefore yield empty / ``None`` summaries.
    """

    candidates: list[AlphaCandidate] = []
    fitness_decisions: list[FitnessDecision] = []
    seen: set[str] = set()

    # Effective candidates: qualified + elite (deduped, payloads from the store).
    for candidate_id in (*state.qualified_pool, *state.elite_pool, *state.rejected_pool):
        if candidate_id in seen:
            continue
        candidate = state.store.get(candidate_id)
        if candidate is None:
            continue
        metrics = _candidate_metrics(candidate)
        if metrics is None:
            continue
        seen.add(candidate_id)
        candidates.append(candidate)
        fitness_decisions.append(
            FitnessDecision(
                candidate_id=candidate_id,
                metrics=metrics,
                stage=candidate.stage,
                qualified_thresholds=metrics,
                elite_thresholds=metrics,
            )
        )

    return build_generation_feedback(
        generation=state.generation,
        candidates=candidates,
        fitness_decisions=fitness_decisions,
        effective_sample_size=2,
        ineffective_sample_size=2,
    )


def _candidate_metrics(candidate: AlphaCandidate) -> FitnessMetrics | None:
    """Return the candidate's stored fitness metrics, or ``None`` when unscored."""

    raw_metrics = candidate.metadata.get("fitness_metrics")
    if raw_metrics is None:
        return None
    return FitnessMetrics.model_validate(raw_metrics)


def build_generation_feedback(
    *,
    generation: int,
    candidates: Sequence[AlphaCandidate],
    fitness_decisions: Sequence[FitnessDecision],
    effective_sample_size: int = 2,
    ineffective_sample_size: int = 2,
) -> GenerationFeedback:
    """Summarize valid and invalid alphas for the next generation.

    Migrated verbatim from ``candidates/feedback.py`` (deleted in 19-06): effective
    = decisions in {QUALIFIED, ELITE} sorted by composite fitness desc (top-N);
    ineffective = REJECTED_BY_FITNESS sorted asc (worst-N).
    """

    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    effective_decisions = [
        decision for decision in fitness_decisions if decision.stage in EFFECTIVE_STAGES
    ]
    ineffective_decisions = [
        decision
        for decision in fitness_decisions
        if decision.stage == CandidateStage.REJECTED_BY_FITNESS
    ]

    effective_decisions.sort(
        key=lambda decision: composite_fitness_score(decision.metrics),
        reverse=True,
    )
    ineffective_decisions.sort(key=lambda decision: composite_fitness_score(decision.metrics))

    effective_samples = [
        _sample_from_decision(decision, candidates_by_id, FeedbackPolarity.EFFECTIVE)
        for decision in effective_decisions[:effective_sample_size]
    ]
    ineffective_samples = [
        _sample_from_decision(decision, candidates_by_id, FeedbackPolarity.INEFFECTIVE)
        for decision in ineffective_decisions[:ineffective_sample_size]
    ]

    return GenerationFeedback(
        generation=generation,
        effective_samples=effective_samples,
        ineffective_samples=ineffective_samples,
        effective_feedback_summary=_join_samples(effective_samples),
        ineffective_feedback_summary=_join_samples(ineffective_samples),
    )


def _sample_from_decision(
    decision: FitnessDecision,
    candidates_by_id: Mapping[str, AlphaCandidate],
    polarity: FeedbackPolarity,
) -> FeedbackSample:
    candidate = candidates_by_id.get(decision.candidate_id)
    rationale = candidate.alpha.rationale if candidate is not None else "Candidate not retained."
    return FeedbackSample(
        candidate_id=decision.candidate_id,
        polarity=polarity,
        stage=decision.stage,
        metrics=decision.metrics,
        summary=(
            f"{decision.candidate_id}: {decision.stage.value}; "
            f"IC={decision.metrics.ic:.4f}, RankIC={decision.metrics.rank_ic:.4f}, "
            f"ICIR={decision.metrics.icir:.4f}, RankICIR={decision.metrics.rank_icir:.4f}, "
            f"MI={decision.metrics.mi:.4f}. Hypothesis: {rationale}"
        ),
    )


def _join_samples(samples: Sequence[FeedbackSample]) -> str | None:
    if not samples:
        return None
    return "\n".join(sample.summary for sample in samples)
