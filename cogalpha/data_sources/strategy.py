"""Phase 13 data-source strategy decision contracts."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataSourcePathId(StrEnum):
    """Known Phase 13 data-source/runtime paths."""

    DIRECT_QLIB = "direct_qlib"
    HF_QUANTAALPHA_QLIB_CSI300 = "hf_quantaalpha_qlib_csi300"
    BAOSTOCK = "baostock"


class DataSourceRole(StrEnum):
    """Claim-safe role assigned to a data-source path."""

    SELECTED_MAIN = "selected_main"
    ENGINEERING_FALLBACK = "engineering_fallback"
    SANITY_OR_NARROW_FALLBACK = "sanity_or_narrow_fallback"


class _StrictModel(BaseModel):
    """Strict persisted Phase 13 contract base."""

    model_config = ConfigDict(extra="forbid")


class DataSourceCandidateAssessment(_StrictModel):
    """One candidate source/runtime path in the Phase 13 comparison."""

    path_id: DataSourcePathId
    label: str = Field(..., min_length=1)
    role: DataSourceRole
    authority_status: str = Field(..., min_length=1)
    source_evidence_needed: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    fallback_allowed: bool
    main_chain_allowed: bool
    claim_status: str = Field(..., min_length=1)
    forbidden_claims: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_role_boundaries(self) -> DataSourceCandidateAssessment:
        if self.role == DataSourceRole.SELECTED_MAIN and not self.main_chain_allowed:
            raise ValueError("selected_main candidate must be main_chain_allowed")
        if self.path_id == DataSourcePathId.HF_QUANTAALPHA_QLIB_CSI300:
            if self.role != DataSourceRole.ENGINEERING_FALLBACK:
                raise ValueError("HF QuantaAlpha/Qlib path must be engineering_fallback")
            if self.main_chain_allowed:
                raise ValueError("HF QuantaAlpha/Qlib path cannot be main-chain allowed")
            required = {"full_paper_reproduction", "qlib_equivalent_backtest_evidence"}
            if not required.issubset(set(self.forbidden_claims)):
                raise ValueError("HF engineering fallback is missing forbidden claims")
            if self.authority_status == "paper_protocol_authority":
                raise ValueError("HF engineering fallback cannot be paper-protocol authority")
        if self.path_id == DataSourcePathId.BAOSTOCK:
            if self.role != DataSourceRole.SANITY_OR_NARROW_FALLBACK:
                raise ValueError("BaoStock path must be sanity_or_narrow_fallback")
            if self.main_chain_allowed:
                raise ValueError("BaoStock cannot be main-chain allowed")
        return self


class Phase13FallbackRule(_StrictModel):
    """Allowed downgrade path and evidence required before using it."""

    rule_id: str = Field(..., min_length=1)
    from_path: DataSourcePathId
    to_path: DataSourcePathId
    trigger: str = Field(..., min_length=1)
    allowed_use: str = Field(..., min_length=1)
    required_evidence: list[str] = Field(default_factory=list)
    claim_status: str = Field(..., min_length=1)
    forbidden_claims: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)


class Phase13DataStrategyDecision(_StrictModel):
    """Machine-readable selected v3 data-source strategy."""

    decision_id: str = Field(..., min_length=1)
    selected_path: DataSourcePathId
    selected_reason: str = Field(..., min_length=1)
    candidates: list[DataSourceCandidateAssessment] = Field(..., min_length=1)
    fallback_rules: list[Phase13FallbackRule] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    next_evidence_gates: list[str] = Field(default_factory=list)
    cogalpha_benchmark_spec: str = Field(..., min_length=1)
    quantaalpha_benchmark_spec: str = Field(..., min_length=1)
    settings_blended: bool = False
    decision_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _precheck_single_selected_main(cls, data):
        if not isinstance(data, dict):
            return data
        candidates = data.get("candidates", [])
        selected_count = 0
        for candidate in candidates:
            if (
                isinstance(candidate, dict)
                and candidate.get("role") == DataSourceRole.SELECTED_MAIN
            ):
                selected_count += 1
            elif isinstance(candidate, DataSourceCandidateAssessment):
                selected_count += candidate.role == DataSourceRole.SELECTED_MAIN
        if selected_count != 1:
            raise ValueError("exactly one selected_main candidate is required")
        return data

    @model_validator(mode="after")
    def _enforce_selected_path(self) -> Phase13DataStrategyDecision:
        ids = [candidate.path_id for candidate in self.candidates]
        duplicate_ids = sorted({path_id.value for path_id in ids if ids.count(path_id) > 1})
        if duplicate_ids:
            raise ValueError(f"duplicate data-source candidate ids: {duplicate_ids}")

        selected = [
            candidate
            for candidate in self.candidates
            if candidate.role == DataSourceRole.SELECTED_MAIN
        ]
        if len(selected) != 1:
            raise ValueError("exactly one selected_main candidate is required")
        if selected[0].path_id != self.selected_path:
            raise ValueError("selected_path must match the selected_main candidate")
        if self.selected_path != DataSourcePathId.DIRECT_QLIB:
            raise ValueError("Phase 13 selected_path must be direct_qlib")
        if self.settings_blended:
            raise ValueError("CogAlpha and QuantaAlpha settings must not be blended")

        required_ids = {"D-01", "D-02", "D-03", "D-04", "D-05", "D-06", "D-07"}
        observed_ids = set(self.decision_ids)
        for candidate in self.candidates:
            observed_ids.update(candidate.decision_ids)
        for rule in self.fallback_rules:
            observed_ids.update(rule.decision_ids)
        missing_ids = sorted(required_ids - observed_ids)
        if missing_ids:
            raise ValueError(f"Phase 13 strategy missing decision ids: {missing_ids}")
        if not self.forbidden_claims:
            raise ValueError("Phase 13 strategy requires forbidden_claims")
        return self

    def candidate_by_id(self, path_id: DataSourcePathId | str) -> DataSourceCandidateAssessment:
        """Return a candidate by path id."""

        resolved = DataSourcePathId(path_id)
        for candidate in self.candidates:
            if candidate.path_id == resolved:
                return candidate
        raise KeyError(f"unknown data-source candidate: {resolved.value}")


def build_phase13_data_strategy_decision() -> Phase13DataStrategyDecision:
    """Build the locked Phase 13 source-strategy decision."""

    return Phase13DataStrategyDecision(
        decision_id="phase13-data-source-strategy-v1",
        selected_path=DataSourcePathId.DIRECT_QLIB,
        selected_reason=(
            "D-01 best-path-first reasoning selects direct Qlib because D-02 and D-07 "
            "make it the strongest source for Qlib instruments, calendar, SH000300 "
            "benchmark returns, adjustment/factor evidence, and exported OHLCV panels."
        ),
        candidates=[
            DataSourceCandidateAssessment(
                path_id=DataSourcePathId.DIRECT_QLIB,
                label="Direct Qlib data/runtime path",
                role=DataSourceRole.SELECTED_MAIN,
                authority_status="preferred_qib_source_for_phase13_backtest_readiness",
                source_evidence_needed=[
                    "Qlib provider_uri fingerprint and data version",
                    "Qlib csi300 point-in-time instruments and trading calendar",
                    "SH000300 benchmark returns from the same Qlib source",
                    "adjusted price or factor/original-price semantics evidence",
                ],
                risks=[
                    "local Qlib data may be absent or incomplete",
                    "adjustment semantics must be proven before price-parity claims",
                    "formal portfolio metric parity remains Phase 14 scope",
                ],
                benefits=[
                    "closest path to the paper's Qlib-based simulation semantics",
                    "single source for instruments, calendar, benchmark, and OHLCV exports",
                    "avoids blending processed HF data with BaoStock raw-source assumptions",
                ],
                fallback_allowed=False,
                main_chain_allowed=True,
                claim_status="selected_main_phase13_data_path_pending_evidence",
                forbidden_claims=[
                    "full_paper_reproduction",
                    "alpha_quality_evidence",
                    "qlib_equivalent_backtest_evidence_before_phase14",
                    "price_parity_without_adjustment_evidence",
                ],
                decision_ids=["D-01", "D-02", "D-05", "D-07"],
                notes=[
                    "Use direct Qlib first unless source evidence fails closed.",
                    "Phase 13 records backtest-readiness evidence, not formal parity.",
                ],
            ),
            DataSourceCandidateAssessment(
                path_id=DataSourcePathId.HF_QUANTAALPHA_QLIB_CSI300,
                label="Hugging Face QuantaAlpha/qlib_csi300 prepared data",
                role=DataSourceRole.ENGINEERING_FALLBACK,
                authority_status="processed_engineering_reference_not_paper_protocol_authority",
                source_evidence_needed=[
                    "HF dataset repository and revision",
                    "coverage and split window evidence",
                    "direct Qlib blocker that justifies downgrade",
                    "gap from CogAlpha paper target and Qlib source semantics",
                ],
                risks=[
                    "processed data from another paper/project",
                    "does not automatically prove CogAlpha paper-protocol authority",
                    "cannot support Qlib-equivalent backtest claims by itself",
                ],
                benefits=[
                    "quick engineering smoke path already represented by existing script",
                    "useful coverage comparison if direct Qlib fails closed",
                    "can exercise the project OHLCV panel contract",
                ],
                fallback_allowed=True,
                main_chain_allowed=False,
                claim_status="scaled_real_runtime_engineering_evidence_only",
                forbidden_claims=[
                    "full_paper_reproduction",
                    "qlib_equivalent_backtest_evidence",
                    "paper_protocol_data_authority",
                    "hidden_cogalpha_quantaalpha_blended_defaults",
                ],
                decision_ids=["D-02", "D-03", "D-05", "D-06"],
                notes=[
                    "Use only after recording the direct Qlib blocker.",
                    "Record HF source, revision, coverage, and forbidden claims.",
                ],
            ),
            DataSourceCandidateAssessment(
                path_id=DataSourcePathId.BAOSTOCK,
                label="BaoStock external market-data source",
                role=DataSourceRole.SANITY_OR_NARROW_FALLBACK,
                authority_status="external_sanity_source_not_main_qlib_chain",
                source_evidence_needed=[
                    "specific sanity-check question BaoStock is answering",
                    "calendar, benchmark, or source evidence missing from direct Qlib",
                    "proof that BaoStock data does not change main-chain semantics",
                ],
                risks=[
                    "would add extra adjustment, calendar, and provenance burden",
                    "raw-source semantics may not match Qlib exported data",
                    "mixing BaoStock OHLCV into the main dataset would hide assumptions",
                ],
                benefits=[
                    "independent sanity check for narrow source or calendar questions",
                    "possible fallback evidence when direct Qlib lacks a verifiable input",
                ],
                fallback_allowed=True,
                main_chain_allowed=False,
                claim_status="sanity_or_narrow_fallback_evidence_only",
                forbidden_claims=[
                    "main_chain_ohlcv_source",
                    "full_paper_reproduction",
                    "qlib_equivalent_backtest_evidence",
                    "hidden_baostock_qlib_blend",
                ],
                decision_ids=["D-04", "D-05"],
                notes=[
                    "Do not blend BaoStock OHLCV into the main dataset",
                    "BaoStock remains outside the main OHLCV chain unless narrowly justified.",
                ],
            ),
        ],
        fallback_rules=[
            Phase13FallbackRule(
                rule_id="direct-qlib-to-hf-engineering",
                from_path=DataSourcePathId.DIRECT_QLIB,
                to_path=DataSourcePathId.HF_QUANTAALPHA_QLIB_CSI300,
                trigger=(
                    "direct Qlib source evidence fails closed or is not reproducible in "
                    "the Phase 13 environment"
                ),
                allowed_use=(
                    "engineering reference, quick smoke fallback, or downgraded scaled "
                    "real-runtime engineering evidence"
                ),
                required_evidence=[
                    "direct Qlib blocker",
                    "HF source and revision",
                    "HF coverage and split evidence",
                    "gap from the full CogAlpha paper target",
                ],
                claim_status="downgraded_engineering_fallback",
                forbidden_claims=[
                    "full_paper_reproduction",
                    "qlib_equivalent_backtest_evidence",
                ],
                decision_ids=["D-03", "D-06"],
            ),
            Phase13FallbackRule(
                rule_id="direct-qlib-to-baostock-sanity",
                from_path=DataSourcePathId.DIRECT_QLIB,
                to_path=DataSourcePathId.BAOSTOCK,
                trigger=(
                    "direct Qlib lacks narrow benchmark, calendar, or source evidence "
                    "that BaoStock can verify without changing main-chain semantics"
                ),
                allowed_use="external sanity check or narrowly justified fallback evidence",
                required_evidence=[
                    "narrow sanity-check purpose",
                    "non-blending explanation",
                    "remaining Qlib parity blockers",
                ],
                claim_status="sanity_evidence_only",
                forbidden_claims=[
                    "main_chain_ohlcv_source",
                    "hidden_baostock_qlib_blend",
                    "full_paper_reproduction",
                ],
                decision_ids=["D-04", "D-05"],
            ),
        ],
        forbidden_claims=[
            "full_paper_reproduction",
            "alpha_quality_evidence",
            "production_trading_evidence",
            "qlib_equivalent_backtest_evidence_before_phase14",
            "hidden_cogalpha_quantaalpha_blended_defaults",
            "hidden_baostock_qlib_blend",
        ],
        next_evidence_gates=[
            "direct Qlib provider_uri and data version fingerprint",
            "Qlib csi300 point-in-time universe membership",
            "Qlib trading calendar and SH000300 benchmark returns",
            "adjusted-price or factor/original-price semantics",
            "split, warmup, 11-trading-day lookahead, and coverage manifests",
        ],
        cogalpha_benchmark_spec="cogalpha_csi300_ohlcv_v1",
        quantaalpha_benchmark_spec="quantaalpha_csi300_ohlcv_v1",
        settings_blended=False,
        decision_ids=["D-01", "D-02", "D-03", "D-04", "D-05", "D-06", "D-07"],
        notes=[
            "CogAlpha and QuantaAlpha specs remain separate named benchmark specs.",
            "No BaoStock dependency or main-chain data blend is introduced by this decision.",
        ],
    )


def write_phase13_data_strategy_json(
    path: str | Path,
    decision: Phase13DataStrategyDecision,
) -> None:
    """Write the Phase 13 data strategy as stable JSON."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(decision.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_phase13_data_strategy_json(path: str | Path) -> Phase13DataStrategyDecision:
    """Load a persisted Phase 13 data strategy JSON artifact."""

    return Phase13DataStrategyDecision.model_validate_json(Path(path).read_text(encoding="utf-8"))


def render_phase13_data_strategy_markdown(decision: Phase13DataStrategyDecision) -> str:
    """Render a human-readable Phase 13 data strategy report."""

    selected = decision.candidate_by_id(decision.selected_path)
    lines = [
        "# Phase 13 Data Strategy",
        "",
        "## Selected Path",
        "",
        f"- Path: `{decision.selected_path.value}`",
        f"- Role: `{selected.role.value}`",
        f"- Reason: {decision.selected_reason}",
        f"- CogAlpha spec: `{decision.cogalpha_benchmark_spec}`",
        f"- QuantaAlpha spec: `{decision.quantaalpha_benchmark_spec}`",
        f"- Settings blended: `{str(decision.settings_blended).lower()}`",
        "",
        "## Candidate Comparison",
        "",
    ]
    for candidate in decision.candidates:
        lines.extend(
            [
                f"### {candidate.label}",
                "",
                f"- Path id: `{candidate.path_id.value}`",
                f"- Role: `{candidate.role.value}`",
                f"- Authority status: {candidate.authority_status}",
                f"- Claim status: {candidate.claim_status}",
                f"- Fallback allowed: `{str(candidate.fallback_allowed).lower()}`",
                f"- Main chain allowed: `{str(candidate.main_chain_allowed).lower()}`",
                "- Benefits:",
                *_bullet_lines(candidate.benefits),
                "- Risks:",
                *_bullet_lines(candidate.risks),
                "- Source evidence needed:",
                *_bullet_lines(candidate.source_evidence_needed),
                "- Decision ids: " + ", ".join(candidate.decision_ids),
                "- Notes:",
                *_bullet_lines(candidate.notes),
                "",
            ]
        )

    lines.extend(["## Fallback Rules", ""])
    for rule in decision.fallback_rules:
        lines.extend(
            [
                f"### {rule.rule_id}",
                "",
                f"- From: `{rule.from_path.value}`",
                f"- To: `{rule.to_path.value}`",
                f"- Trigger: {rule.trigger}",
                f"- Allowed use: {rule.allowed_use}",
                f"- Claim status: {rule.claim_status}",
                "- Required evidence:",
                *_bullet_lines(rule.required_evidence),
                "- Forbidden claims:",
                *_bullet_lines(rule.forbidden_claims),
                "",
            ]
        )

    lines.extend(
        [
            "## Forbidden Claims",
            "",
            *_bullet_lines(decision.forbidden_claims),
            "",
            "## Next Evidence Gates",
            "",
            *_bullet_lines(decision.next_evidence_gates),
            "",
            "## Decision Evidence",
            "",
            *_bullet_lines(decision.decision_ids),
            "",
        ]
    )
    return "\n".join(lines)


def write_phase13_data_strategy_markdown(
    path: str | Path,
    decision: Phase13DataStrategyDecision,
) -> None:
    """Write a human-readable Phase 13 data strategy report."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_phase13_data_strategy_markdown(decision), encoding="utf-8")


def _bullet_lines(items: list[str]) -> list[str]:
    if not items:
        return ["  - None recorded."]
    return [f"  - {item}" for item in items]
