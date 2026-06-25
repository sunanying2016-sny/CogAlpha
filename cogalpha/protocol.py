"""Single source of truth for every paper search/agent system variable.

`ProtocolConfig` is the v4.0 engine's authoritative, strict configuration model
for the paper's multi-agent search protocol. It follows the strict-pydantic
pattern established in ``cogalpha/benchmark/specs.py`` (``ConfigDict(extra="forbid")``
+ ``@model_validator(mode="after")``) and exposes two explicit named constructors:

- :meth:`ProtocolConfig.paper_default` pins every system field to its paper value
  and rejects any value below the paper default (PROTO-04a / D-01).
- :meth:`ProtocolConfig.validation` is an explicit, named profile that permits
  smaller absolute counts (and only those) while enforcing the D-03 protocol
  floors -- it is *never* the silent default (D-01).

Both constructors return the *same* ``ProtocolConfig`` type and run through the
*same* structural-invariant validator, so a reduced-config validation run
exercises the same full-scale code path as a paper-default run (G1).

Design boundaries:

- Structural ratios / cadence and selection semantics are paper-pinned in BOTH
  profiles and may never be tuned: ``children_pool == 3 * parent_pool``,
  ``injection_every == 2``, ``elite_carry == 2``,
  ``generations == inner_subcycles * subcycle_length``, the 65/80 percentile
  thresholds, and ``NaN > 30%`` reject (D-02).
- The data + evaluation contract (top-50/drop-5, costs, label, benchmark,
  horizon) is expressed by *reference*: ``ProtocolConfig`` embeds a resolved
  :class:`~cogalpha.benchmark.specs.BenchmarkSpec` snapshot for reproducibility
  but never copies its fields. ``BenchmarkSpec`` remains the single source of
  truth for those fields (D-05).

D-04 (two layers of "no cap below default"): PROTO-04's config-level "reject any
value below the paper default" applies *only* in :meth:`paper_default`; the
code-level "no hardcoded ``min()`` / ceiling below ``ProtocolConfig`` on the hot
paths" is a contract of BOTH profiles, enforced separately by the Phase 22
VAL-02 static check. This module delivers only the config-level layer.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cogalpha.benchmark.presets import COGALPHA_PRESET_ID, get_benchmark_spec
from cogalpha.benchmark.specs import BenchmarkSpec
from cogalpha.config import FitnessGateConfig
from cogalpha.schemas import FitnessMetrics

# Paper-pinned structural / selection constants (D-02). Defined once here so the
# defaults and the validator share a single literal source.
_PINNED_INJECTION_EVERY = 2
_PINNED_ELITE_CARRY = 2
_PINNED_QUALIFIED_PERCENTILE = 0.65
_PINNED_ELITE_PERCENTILE = 0.80
_PINNED_NAN_REJECT_THRESHOLD = 0.30

# Absolute protocol floors for the validation profile (D-03). These guarantee a
# reduced-config run still exercises multi-generation + per-gen adaptive
# regeneration + injection (fires >= 2x) -- the Phase 22 acceptance condition.
_FLOOR_GENERATIONS = 4
_FLOOR_INNER_SUBCYCLES = 2
_FLOOR_PARENT_POOL = 2
_FLOOR_DOMAIN_AGENTS = 1

# S&P500 benchmark id for the App A.4 per-metric minima (no preset module exists
# for it yet; the local literal is documented here as the single source until a
# resolved BenchmarkSpec preset lands). App A.4 gives CSI300 + S&P500 only.
SP500_PRESET_ID = "sp500"

# App A.4 S&P500 mutual-information minimum: the ONLY metric that differs from
# CSI300 (0.02 -> 0.012; "harder to mine alpha in a more efficient market").
_SP500_MI_MINIMUM = 0.012

# Override keys the validation profile accepts: absolute counts + tunable knobs
# (factors_per_request, quality_repair_attempts) only (D-02).
_VALIDATION_ALLOWED_OVERRIDES = frozenset(
    {
        "domain_agents",
        "initial_pool",
        "parent_pool",
        "children_pool",
        "generations",
        "inner_subcycles",
        "subcycle_length",
        "factors_per_request",
        "quality_repair_attempts",
    }
)


class FitnessMinimaPair(BaseModel):
    """Qualified + elite fitness minima for one benchmark (App A.4).

    The fitness gate needs BOTH the qualified and the elite per-metric floor for
    a benchmark; this pair carries them together, keyed by benchmark id in
    :attr:`ProtocolConfig.per_metric_minima`.
    """

    model_config = ConfigDict(extra="forbid")

    qualified: FitnessMetrics
    elite: FitnessMetrics


def _default_per_metric_minima() -> dict[str, FitnessMinimaPair]:
    """Per-benchmark qualified+elite fitness minima, keyed by benchmark id.

    Reuses the paper-defined CSI300 minima already encoded in
    ``config.py:FitnessGateConfig`` (the single source for those numbers) and
    seeds the App A.4 S&P500 entry, which differs from CSI300 ONLY in MI
    (0.02 -> 0.012); IC/RankIC/ICIR/RankICIR are identical across both markets.
    """

    gate = FitnessGateConfig()
    return {
        COGALPHA_PRESET_ID: FitnessMinimaPair(
            qualified=gate.qualified_minima,
            elite=gate.elite_minima,
        ),
        SP500_PRESET_ID: FitnessMinimaPair(
            qualified=gate.qualified_minima.model_copy(update={"mi": _SP500_MI_MINIMUM}),
            elite=gate.elite_minima.model_copy(update={"mi": _SP500_MI_MINIMUM}),
        ),
    }


def _default_benchmark_spec() -> BenchmarkSpec:
    """Embedded resolved CSI300 paper-contract snapshot (D-05)."""

    return get_benchmark_spec(COGALPHA_PRESET_ID)


class ProtocolConfig(BaseModel):
    """Strict single source of truth for the paper search/agent protocol."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- Absolute counts: shrinkable in `validation`, pinned in `paper_default`.
    domain_agents: int = Field(default=21, ge=1)
    initial_pool: int = Field(default=80, ge=1)
    parent_pool: int = Field(default=32, ge=1)
    children_pool: int = Field(default=96, ge=1)
    generations: int = Field(default=24, ge=1)
    inner_subcycles: int = Field(default=3, ge=1)
    subcycle_length: int = Field(default=8, ge=1)

    # --- Per-request generation count: fills C.1 {num_per_request}. Tunable in
    # `validation`; paper_default 4 so domain_agents * factors_per_request >=
    # initial_pool (21 * 4 = 84 >= 80). Not PROTO-04 pinned (Discretion).
    factors_per_request: int = Field(default=4, ge=1)

    # --- Quality-checker repair retry budget (D-02). A tunable knob, NOT a
    # PROTO-04-pinned paper-structural variable: the validation profile may lower
    # it (even to 0) and there is NO floor clamp blocking that.
    quality_repair_attempts: int = Field(default=3, ge=0)

    # --- Structural / selection semantics: paper-pinned in BOTH profiles (D-02).
    injection_every: int = Field(default=_PINNED_INJECTION_EVERY)
    elite_carry: int = Field(default=_PINNED_ELITE_CARRY)
    qualified_percentile: float = Field(default=_PINNED_QUALIFIED_PERCENTILE, ge=0, le=1)
    elite_percentile: float = Field(default=_PINNED_ELITE_PERCENTILE, ge=0, le=1)
    nan_reject_threshold: float = Field(default=_PINNED_NAN_REJECT_THRESHOLD, ge=0, le=1)

    # --- Per-benchmark fitness minima: qualified+elite pair per benchmark (App A.4).
    per_metric_minima: dict[str, FitnessMinimaPair] = Field(
        default_factory=_default_per_metric_minima
    )

    # --- Data / evaluation contract: referenced, never copied (D-05).
    benchmark_spec: BenchmarkSpec = Field(default_factory=_default_benchmark_spec)

    @model_validator(mode="after")
    def _enforce_structural_invariants(self) -> ProtocolConfig:
        """Enforce the paper-pinned structural invariants (D-02/D-03)."""

        if self.children_pool != 3 * self.parent_pool:
            raise ValueError(
                "children_pool must equal 3 x parent_pool (paper-pinned, D-02)"
            )
        if self.generations != self.inner_subcycles * self.subcycle_length:
            raise ValueError(
                "generations must equal inner_subcycles x subcycle_length (D-02)"
            )
        if self.injection_every != _PINNED_INJECTION_EVERY:
            raise ValueError("injection_every is paper-pinned to 2 (D-02)")
        if self.elite_carry != _PINNED_ELITE_CARRY:
            raise ValueError("elite_carry is paper-pinned to 2 (D-02)")
        if self.qualified_percentile != _PINNED_QUALIFIED_PERCENTILE:
            raise ValueError("qualified_percentile is paper-pinned to 0.65 (D-02)")
        if self.elite_percentile != _PINNED_ELITE_PERCENTILE:
            raise ValueError("elite_percentile is paper-pinned to 0.80 (D-02)")
        if self.nan_reject_threshold != _PINNED_NAN_REJECT_THRESHOLD:
            raise ValueError("nan_reject_threshold is paper-pinned to 0.30 (D-02)")
        if self.initial_pool < self.parent_pool:
            raise ValueError("initial_pool must be >= parent_pool (D-03)")
        if self.factors_per_request * self.domain_agents < self.initial_pool:
            raise ValueError(
                "factors_per_request * domain_agents must be >= initial_pool "
                "so the generation stage can fill the initial pool (STAGE-01)"
            )
        return self

    @classmethod
    def paper_default(cls) -> ProtocolConfig:
        """Construct the paper-default profile (all paper values, PROTO-04a).

        Takes no system overrides: any reduced configuration must go through the
        explicit :meth:`validation` profile, so paper_default can never silently
        carry a system count below its paper default (D-01 / PROTO-04a).
        """

        return cls()

    @classmethod
    def validation(cls, **overrides: Any) -> ProtocolConfig:
        """Construct the explicit, named validation profile (D-01).

        Only absolute counts may be overridden (D-02); structural / selection
        keys are paper-pinned and rejected here. The D-03 protocol floors are
        enforced after construction so a reduced-config run still exercises
        multi-generation + adaptive regeneration + injection (the Phase 22
        acceptance condition). Returns the same ``ProtocolConfig`` type and runs
        the same structural-invariant validator as :meth:`paper_default` (G1).
        """

        illegal = set(overrides) - _VALIDATION_ALLOWED_OVERRIDES
        if illegal:
            raise ValueError(
                "structural/semantic fields are paper-pinned, cannot override "
                f"{sorted(illegal)} (D-02)"
            )

        cfg = cls(**overrides)

        if cfg.generations < _FLOOR_GENERATIONS:
            raise ValueError(
                f"generations must be >= {_FLOOR_GENERATIONS} (validation floors, D-03)"
            )
        if cfg.inner_subcycles < _FLOOR_INNER_SUBCYCLES:
            raise ValueError(
                f"inner_subcycles must be >= {_FLOOR_INNER_SUBCYCLES} "
                "(validation floors, D-03)"
            )
        if cfg.parent_pool < _FLOOR_PARENT_POOL:
            raise ValueError(
                f"parent_pool must be >= {_FLOOR_PARENT_POOL} "
                "(=> children_pool >= 6) (validation floors, D-03)"
            )
        if cfg.domain_agents < _FLOOR_DOMAIN_AGENTS:
            raise ValueError(
                f"domain_agents must be >= {_FLOOR_DOMAIN_AGENTS} "
                "(validation floors, D-03)"
            )
        # initial_pool >= parent_pool is already enforced by the shared validator.
        return cfg
