"""Runtime schemas exchanged by CogAlpha DAG nodes."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cogalpha.alpha_contract import (
    DEFAULT_ALPHA_LIBRARY_ALIASES,
    DEFAULT_OHLCV_COLUMNS,
    DEFAULT_OHLCV_COLUMNS_DESC,
    DEFAULT_OHLCV_FACTOR_DESCRIPTIONS,
)

OHLCVColumn = Literal["open", "high", "low", "close", "volume"]


class CandidateStage(StrEnum):
    """Lifecycle stage for an alpha candidate."""

    GENERATED = "generated"
    REPAIRED = "repaired"
    ACCEPTED_BY_QUALITY = "accepted_by_quality"
    REJECTED_BY_QUALITY = "rejected_by_quality"
    QUALIFIED = "qualified"
    ELITE = "elite"
    REJECTED_BY_FITNESS = "rejected_by_fitness"


class SkillKind(StrEnum):
    """Kinds of standard skills used by the CogAlpha DAG."""

    DOMAIN_AGENT = "domain_agent"
    QUALITY_CHECKER = "quality_checker"
    EVOLUTION_OPERATOR = "evolution_operator"


class EvolutionOperation(StrEnum):
    """Thinking evolution operation applied to parent candidates."""

    MUTATION = "mutation"
    CROSSOVER = "crossover"
    CROSSOVER_THEN_MUTATION = "crossover_then_mutation"


class FeedbackPolarity(StrEnum):
    """Whether a feedback sample teaches from success or failure."""

    EFFECTIVE = "effective"
    INEFFECTIVE = "ineffective"


class QualityVerdict(StrEnum):
    """Semantic verdict emitted by quality checker skills."""

    ACCEPT = "accept"
    REPAIR = "repair"
    REJECT = "reject"


class GuardStatus(StrEnum):
    """Deterministic guard result."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


class SkillRef(BaseModel):
    """Reference to a project-local standard skill."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    kind: SkillKind


class AlphaFunction(BaseModel):
    """Executable Python function that computes an alpha factor."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., pattern=r"^factor_[a-zA-Z0-9_]+$")
    code: str = Field(..., min_length=1)
    formula: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    required_columns: list[OHLCVColumn] = Field(
        default_factory=lambda: list(DEFAULT_OHLCV_COLUMNS)
    )
    allowed_libraries: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALPHA_LIBRARY_ALIASES)
    )


class EvolutionLineage(BaseModel):
    """Trace of how a candidate was produced."""

    model_config = ConfigDict(extra="forbid")

    operation: EvolutionOperation | None = None
    parent_ids: list[str] = Field(default_factory=list)
    generation: int = Field(default=0, ge=0)
    agent_skill: str | None = None
    guidance_mode: str | None = None


class AlphaCandidate(BaseModel):
    """Generated alpha factor plus metadata needed for checking and evolution."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(..., min_length=1)
    alpha: AlphaFunction
    stage: CandidateStage = CandidateStage.GENERATED
    lineage: EvolutionLineage = Field(default_factory=EvolutionLineage)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class DomainAgentRequest(BaseModel):
    """Runtime payload sent to one domain agent skill."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str
    paper_agent_name: str
    level: int = Field(..., ge=1, le=7)
    layer: str
    focus: str
    factor_type: str | None = None
    num_candidates: int = Field(default=1, ge=1)
    generation: int = Field(default=0, ge=0)
    guidance_mode: str | None = None
    target_horizon_days: int = Field(default=10, ge=1)
    target_label: str = "10-day forward returns"
    input_frequency: str = "daily"
    columns_num: int = Field(default=len(DEFAULT_OHLCV_COLUMNS), ge=1)
    columns_desc: str = DEFAULT_OHLCV_COLUMNS_DESC
    existing_factor_descriptions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_OHLCV_FACTOR_DESCRIPTIONS)
    )
    effective_feedback_summary: str | None = None
    ineffective_feedback_summary: str | None = None
    available_columns: list[OHLCVColumn] = Field(
        default_factory=lambda: list(DEFAULT_OHLCV_COLUMNS)
    )


class AlphaCandidateBatch(BaseModel):
    """Batch returned by a generation or evolution skill."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[AlphaCandidate] = Field(default_factory=list)


class EvolutionSkillRequest(BaseModel):
    """Runtime payload sent to a thinking-evolution skill."""

    model_config = ConfigDict(extra="forbid")

    operation: EvolutionOperation
    parents: list[AlphaCandidate] = Field(..., min_length=1)
    generation: int = Field(..., ge=0)
    effective_feedback_summary: str | None = None
    ineffective_feedback_summary: str | None = None


class GuardIssue(BaseModel):
    """One deterministic validation issue."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    severity: Literal["error", "warning"] = "error"
    location: str | None = None


class GuardReport(BaseModel):
    """Structured report emitted by a deterministic guard node."""

    model_config = ConfigDict(extra="forbid")

    guard_name: str = Field(..., min_length=1)
    status: GuardStatus
    issues: list[GuardIssue] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QualityDecision(BaseModel):
    """Semantic quality-checking decision from a quality checker skill."""

    model_config = ConfigDict(extra="forbid")

    skill: SkillRef
    verdict: QualityVerdict
    practical_soundness: str = Field(..., min_length=1)
    feedback: str = Field(..., min_length=1)
    repaired_candidate: AlphaCandidate | None = None


class QualitySkillRequest(BaseModel):
    """Runtime payload sent to one quality checker skill."""

    model_config = ConfigDict(extra="forbid")

    candidate: AlphaCandidate
    guard_reports: list[GuardReport] = Field(default_factory=list)
    previous_decisions: list[QualityDecision] = Field(default_factory=list)
    feedback: str | None = None
    attempt: int = Field(default=0, ge=0)


class FitnessMetrics(BaseModel):
    """Predictive metrics used by the paper-defined fitness gate."""

    model_config = ConfigDict(extra="forbid")

    ic: float
    rank_ic: float
    icir: float
    rank_icir: float
    mi: float


class FitnessDecision(BaseModel):
    """Fitness gate classification for one candidate."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    metrics: FitnessMetrics
    stage: CandidateStage
    qualified_thresholds: FitnessMetrics
    elite_thresholds: FitnessMetrics


class CandidateEvaluationResult(BaseModel):
    """Structured result from panel-backed candidate evaluation."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(..., min_length=1)
    metrics: FitnessMetrics | None = None
    guard_report: GuardReport | None = None
    error: str | None = None
    cache_hit: bool = False
    data_version: str | None = None


class FeedbackSample(BaseModel):
    """One candidate summary used for adaptive generation feedback."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    polarity: FeedbackPolarity
    stage: CandidateStage
    summary: str
    metrics: FitnessMetrics | None = None


class GenerationFeedback(BaseModel):
    """Adaptive generation memory passed into later skill invocations."""

    model_config = ConfigDict(extra="forbid")

    generation: int = Field(default=0, ge=0)
    effective_samples: list[FeedbackSample] = Field(default_factory=list)
    ineffective_samples: list[FeedbackSample] = Field(default_factory=list)
    effective_feedback_summary: str | None = None
    ineffective_feedback_summary: str | None = None


class DAGNodeResult(BaseModel):
    """Common envelope for DAG node outputs."""

    model_config = ConfigDict(extra="forbid")

    node_name: str = Field(..., min_length=1)
    candidates: list[AlphaCandidate] = Field(default_factory=list)
    guard_reports: list[GuardReport] = Field(default_factory=list)
    quality_decisions: list[QualityDecision] = Field(default_factory=list)
    fitness_decisions: list[FitnessDecision] = Field(default_factory=list)
    evaluation_results: list[CandidateEvaluationResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CogAlphaState(BaseModel):
    """Legacy MVP-loop state (superseded by state.py:CogAlphaState).

    Retained until its harness/runner consumers are removed in Wave 2.
    """

    model_config = ConfigDict(extra="forbid")

    generation: int = Field(default=0, ge=0)
    candidates: list[AlphaCandidate] = Field(default_factory=list)
    qualified_pool: list[AlphaCandidate] = Field(default_factory=list)
    elite_pool: list[AlphaCandidate] = Field(default_factory=list)
    rejected_pool: list[AlphaCandidate] = Field(default_factory=list)
    feedback: GenerationFeedback = Field(default_factory=GenerationFeedback)
    node_history: list[DAGNodeResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
