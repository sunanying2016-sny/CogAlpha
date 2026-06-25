"""Paper-defined skill registry for the CogAlpha seven-level hierarchy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cogalpha.schemas import SkillKind, SkillRef

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "skills"


@dataclass(frozen=True)
class DomainAgentSpec:
    """Static metadata for one paper-defined domain agent."""

    skill_name: str
    paper_name: str
    level: int
    layer: str
    focus: str

    @property
    def skill_path(self) -> Path:
        return SKILLS_ROOT / self.skill_name / "SKILL.md"

    def to_skill_ref(self) -> SkillRef:
        return SkillRef(
            name=self.skill_name,
            path=str(self.skill_path.relative_to(PROJECT_ROOT)),
            kind=SkillKind.DOMAIN_AGENT,
        )


DOMAIN_AGENT_SPECS: tuple[DomainAgentSpec, ...] = (
    DomainAgentSpec(
        "alpha-market-cycle",
        "AgentMarketCycle",
        1,
        "Market Structure & Cycle",
        "Long-term trends, market phases, and cyclical state transitions.",
    ),
    DomainAgentSpec(
        "alpha-volatility-regime",
        "AgentVolatilityRegime",
        1,
        "Market Structure & Cycle",
        "Volatility regimes and state-dependent market phases.",
    ),
    DomainAgentSpec(
        "alpha-tail-risk",
        "AgentTailRisk",
        2,
        "Extreme Risk & Fragility",
        "Tail-risk exposure and stress accumulation.",
    ),
    DomainAgentSpec(
        "alpha-crash-predictor",
        "AgentCrashPredictor",
        2,
        "Extreme Risk & Fragility",
        "Crash precursors and regime breakdown signals.",
    ),
    DomainAgentSpec(
        "alpha-liquidity",
        "AgentLiquidity",
        3,
        "Price-Volume Dynamics",
        "Liquidity, price impact, and volume-adjusted movement.",
    ),
    DomainAgentSpec(
        "alpha-order-imbalance",
        "AgentOrderImbalance",
        3,
        "Price-Volume Dynamics",
        "Directional pressure inferred from OHLCV behavior.",
    ),
    DomainAgentSpec(
        "alpha-price-volume-coherence",
        "AgentPriceVolumeCoherence",
        3,
        "Price-Volume Dynamics",
        "Coherence between price movement and volume behavior.",
    ),
    DomainAgentSpec(
        "alpha-volume-structure",
        "AgentVolumeStructure",
        3,
        "Price-Volume Dynamics",
        "Temporal structure and abnormality in volume patterns.",
    ),
    DomainAgentSpec(
        "alpha-daily-trend",
        "AgentDailyTrend",
        4,
        "Price-Volatility Behavior",
        "Directional persistence and multi-day momentum strength.",
    ),
    DomainAgentSpec(
        "alpha-reversal",
        "AgentReversal",
        4,
        "Price-Volatility Behavior",
        "Mean reversion and short-term overreaction corrections.",
    ),
    DomainAgentSpec(
        "alpha-range-vol",
        "AgentRangeVol",
        4,
        "Price-Volatility Behavior",
        "Range-based volatility compression and expansion.",
    ),
    DomainAgentSpec(
        "alpha-lag-response",
        "AgentLagResponse",
        4,
        "Price-Volatility Behavior",
        "Delayed adjustment between volatility, volume, and returns.",
    ),
    DomainAgentSpec(
        "alpha-vol-asymmetry",
        "AgentVolAsymmetry",
        4,
        "Price-Volatility Behavior",
        "Asymmetric volatility between upward and downward moves.",
    ),
    DomainAgentSpec(
        "alpha-drawdown",
        "AgentDrawdown",
        5,
        "Multi-Scale Complexity",
        "Drawdown depth, duration, and recovery geometry.",
    ),
    DomainAgentSpec(
        "alpha-fractal",
        "AgentFractal",
        5,
        "Multi-Scale Complexity",
        "Multi-scale roughness and long-memory characteristics.",
    ),
    DomainAgentSpec(
        "alpha-regime-gating",
        "AgentRegimeGating",
        6,
        "Stability & Regime-Gating",
        "Adaptive gates based on volatility, trend, or liquidity states.",
    ),
    DomainAgentSpec(
        "alpha-stability",
        "AgentStability",
        6,
        "Stability & Regime-Gating",
        "Temporal consistency, persistence, and smoothness.",
    ),
    DomainAgentSpec(
        "alpha-bar-shape",
        "AgentBarShape",
        7,
        "Geometric & Fusion",
        "Candlestick body, shadow, symmetry, and shape descriptors.",
    ),
    DomainAgentSpec(
        "alpha-creative",
        "AgentCreative",
        7,
        "Geometric & Fusion",
        "Nonlinear transformations and novel feature representations.",
    ),
    DomainAgentSpec(
        "alpha-composite",
        "AgentComposite",
        7,
        "Geometric & Fusion",
        "Multi-factor fusion, synergy, and orthogonal combinations.",
    ),
    DomainAgentSpec(
        "alpha-herding",
        "AgentHerding",
        7,
        "Geometric & Fusion",
        "Crowding behavior and directional consensus in OHLCV dynamics.",
    ),
)


QUALITY_SKILLS: tuple[SkillRef, ...] = (
    SkillRef(
        name="alpha-code-quality",
        path="skills/alpha-code-quality/SKILL.md",
        kind=SkillKind.QUALITY_CHECKER,
    ),
    SkillRef(
        name="alpha-code-repair",
        path="skills/alpha-code-repair/SKILL.md",
        kind=SkillKind.QUALITY_CHECKER,
    ),
    SkillRef(
        name="alpha-judge",
        path="skills/alpha-judge/SKILL.md",
        kind=SkillKind.QUALITY_CHECKER,
    ),
    SkillRef(
        name="alpha-logic-improvement",
        path="skills/alpha-logic-improvement/SKILL.md",
        kind=SkillKind.QUALITY_CHECKER,
    ),
)


EVOLUTION_SKILLS: tuple[SkillRef, ...] = (
    SkillRef(
        name="alpha-mutation",
        path="skills/alpha-mutation/SKILL.md",
        kind=SkillKind.EVOLUTION_OPERATOR,
    ),
    SkillRef(
        name="alpha-crossover",
        path="skills/alpha-crossover/SKILL.md",
        kind=SkillKind.EVOLUTION_OPERATOR,
    ),
)


def get_domain_skill_refs() -> tuple[SkillRef, ...]:
    """Return all paper-defined domain agent skill references."""

    return tuple(spec.to_skill_ref() for spec in DOMAIN_AGENT_SPECS)


def all_skill_refs() -> tuple[SkillRef, ...]:
    """Return every standard skill used by the MVP loop."""

    return get_domain_skill_refs() + QUALITY_SKILLS + EVOLUTION_SKILLS
