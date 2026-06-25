"""Diagnostic-only Phase 13 smoke artifact contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

PHASE13_DIAGNOSTIC_FORBIDDEN_CLAIMS: tuple[str, ...] = (
    "full_paper_reproduction",
    "paper_protocol_data_evidence",
    "qlib_equivalent_backtest_evidence",
    "alpha_quality_evidence",
    "production_trading_evidence",
)
PHASE13_DIAGNOSTIC_DECISION_IDS: tuple[str, ...] = (
    "D-03",
    "D-06",
    "D-09",
    "D-17",
    "D-18",
)
PHASE13_DIAGNOSTIC_SCOPE_SENTENCE = "This package proves data/interface closure only"


class _StrictModel(BaseModel):
    """Strict persisted Phase 13 diagnostic contract base."""

    model_config = ConfigDict(extra="forbid")


class Phase13DiagnosticClaimPolicy(_StrictModel):
    """Claim policy that keeps diagnostic smoke artifacts non-promotable."""

    diagnostic_only: bool = True
    claim_status: str = "diagnostic_only"
    forbidden_claims: list[str] = Field(
        default_factory=lambda: list(PHASE13_DIAGNOSTIC_FORBIDDEN_CLAIMS)
    )
    decision_ids: list[str] = Field(default_factory=lambda: list(PHASE13_DIAGNOSTIC_DECISION_IDS))
    scope_statement: str = PHASE13_DIAGNOSTIC_SCOPE_SENTENCE

    @model_validator(mode="after")
    def _enforce_diagnostic_claim_policy(self) -> Phase13DiagnosticClaimPolicy:
        _require_diagnostic_only(self.diagnostic_only, self.claim_status)
        _require_items(
            "diagnostic claim policy forbidden_claims",
            self.forbidden_claims,
            PHASE13_DIAGNOSTIC_FORBIDDEN_CLAIMS,
        )
        _require_items(
            "diagnostic claim policy decision_ids",
            self.decision_ids,
            PHASE13_DIAGNOSTIC_DECISION_IDS,
        )
        if PHASE13_DIAGNOSTIC_SCOPE_SENTENCE not in self.scope_statement:
            raise ValueError("diagnostic claim policy must state data/interface closure only")
        return self


class Phase13SmokeDataSourceEvidence(_StrictModel):
    """Source and downgrade evidence for one diagnostic smoke package."""

    data_source: str = Field(..., min_length=1)
    direct_qlib_blocker: str | None = None
    hf_source_revision: str | None = None
    hf_coverage_summary: dict[str, Any] | None = None
    paper_target_gap: str = Field(..., min_length=1)
    downgrade_status: str = Field(..., min_length=1)
    coverage_summary: dict[str, Any] = Field(default_factory=dict)
    forbidden_claims: list[str] = Field(
        default_factory=lambda: list(PHASE13_DIAGNOSTIC_FORBIDDEN_CLAIMS)
    )
    baostock_blend_status: str = "not_used"
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_source_evidence(self) -> Phase13SmokeDataSourceEvidence:
        _require_items(
            "diagnostic source evidence forbidden_claims",
            self.forbidden_claims,
            (
                "full_paper_reproduction",
                "paper_protocol_data_evidence",
                "qlib_equivalent_backtest_evidence",
            ),
        )
        if self.baostock_blend_status != "not_used":
            if "baostock" not in self.baostock_blend_status.lower():
                raise ValueError("BaoStock sanity/fallback evidence must be explicit if used")
        if self.data_source == "hf_quantaalpha_qlib_csi300":
            missing = [
                name
                for name, value in (
                    ("direct_qlib_blocker", self.direct_qlib_blocker),
                    ("hf_source_revision", self.hf_source_revision),
                    ("hf_coverage_summary", self.hf_coverage_summary),
                    ("paper_target_gap", self.paper_target_gap),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"HF diagnostic fallback evidence missing fields: {missing}")
            if self.downgrade_status not in {
                "engineering_fallback",
                "diagnostic_engineering_fallback",
            }:
                raise ValueError("HF diagnostic fallback must record engineering downgrade")
        return self


class Phase13DiagnosticSmokeManifest(_StrictModel):
    """Diagnostic smoke manifest that cannot support paper-protocol claims."""

    manifest_id: str = "phase13-diagnostic-smoke"
    diagnostic_only: bool = True
    claim_status: str = "diagnostic_only"
    data_source: str = Field(..., min_length=1)
    asset_count: int = Field(..., ge=0)
    short_window_start: str = Field(..., min_length=1)
    short_window_end: str = Field(..., min_length=1)
    source_evidence: Phase13SmokeDataSourceEvidence
    claim_policy: Phase13DiagnosticClaimPolicy = Field(
        default_factory=Phase13DiagnosticClaimPolicy
    )
    forbidden_claims: list[str] = Field(
        default_factory=lambda: list(PHASE13_DIAGNOSTIC_FORBIDDEN_CLAIMS)
    )
    decision_ids: list[str] = Field(default_factory=lambda: list(PHASE13_DIAGNOSTIC_DECISION_IDS))
    notes: list[str] = Field(default_factory=lambda: [PHASE13_DIAGNOSTIC_SCOPE_SENTENCE])

    @model_validator(mode="after")
    def _enforce_manifest_claim_boundary(self) -> Phase13DiagnosticSmokeManifest:
        _require_diagnostic_only(self.diagnostic_only, self.claim_status)
        _require_items(
            "diagnostic smoke manifest forbidden_claims",
            self.forbidden_claims,
            PHASE13_DIAGNOSTIC_FORBIDDEN_CLAIMS,
        )
        _require_items(
            "diagnostic smoke manifest decision_ids",
            self.decision_ids,
            PHASE13_DIAGNOSTIC_DECISION_IDS,
        )
        if self.claim_policy.claim_status != self.claim_status:
            raise ValueError("claim policy status must match diagnostic manifest status")
        if PHASE13_DIAGNOSTIC_SCOPE_SENTENCE not in " ".join(self.notes):
            raise ValueError("diagnostic smoke manifest must state data/interface closure only")
        return self


class Phase13DiagnosticProvenanceReport(_StrictModel):
    """Human-review provenance report for diagnostic-only smoke evidence."""

    report_id: str = "phase13-diagnostic-provenance-report"
    manifest_id: str = Field(..., min_length=1)
    diagnostic_only: bool = True
    claim_status: str = "diagnostic_only"
    source_evidence: Phase13SmokeDataSourceEvidence
    direct_qlib_blocker: str = Field(..., min_length=1)
    paper_target_gap: str = Field(..., min_length=1)
    forbidden_claims: list[str] = Field(
        default_factory=lambda: list(PHASE13_DIAGNOSTIC_FORBIDDEN_CLAIMS)
    )
    decision_ids: list[str] = Field(default_factory=lambda: list(PHASE13_DIAGNOSTIC_DECISION_IDS))
    diagnostic_scope: str = PHASE13_DIAGNOSTIC_SCOPE_SENTENCE
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_report_claim_boundary(self) -> Phase13DiagnosticProvenanceReport:
        _require_diagnostic_only(self.diagnostic_only, self.claim_status)
        _require_items(
            "diagnostic provenance report forbidden_claims",
            self.forbidden_claims,
            PHASE13_DIAGNOSTIC_FORBIDDEN_CLAIMS,
        )
        _require_items(
            "diagnostic provenance report decision_ids",
            self.decision_ids,
            PHASE13_DIAGNOSTIC_DECISION_IDS,
        )
        if PHASE13_DIAGNOSTIC_SCOPE_SENTENCE not in self.diagnostic_scope:
            raise ValueError("diagnostic provenance report must state data/interface closure only")
        return self


def build_phase13_diagnostic_smoke_manifest(
    *,
    source_evidence: Phase13SmokeDataSourceEvidence,
    asset_count: int,
    short_window_start: str,
    short_window_end: str,
    data_source: str | None = None,
) -> Phase13DiagnosticSmokeManifest:
    """Build a diagnostic-only smoke manifest with fail-closed claim labels."""

    return Phase13DiagnosticSmokeManifest(
        data_source=data_source or source_evidence.data_source,
        asset_count=asset_count,
        short_window_start=short_window_start,
        short_window_end=short_window_end,
        source_evidence=source_evidence,
    )


def build_phase13_diagnostic_provenance_report(
    manifest: Phase13DiagnosticSmokeManifest,
) -> Phase13DiagnosticProvenanceReport:
    """Build a diagnostic provenance report from a validated smoke manifest."""

    blocker = manifest.source_evidence.direct_qlib_blocker or "direct Qlib blocker not supplied"
    return Phase13DiagnosticProvenanceReport(
        manifest_id=manifest.manifest_id,
        source_evidence=manifest.source_evidence,
        direct_qlib_blocker=blocker,
        paper_target_gap=manifest.source_evidence.paper_target_gap,
        notes=[
            "Diagnostic package is excluded from Phase 14 portfolio parity.",
            "Formal AER/IR, transaction-cost, and metric parity remain out of scope.",
        ],
    )


def write_phase13_diagnostic_smoke_manifest_json(
    path: str | Path,
    manifest: Phase13DiagnosticSmokeManifest,
) -> None:
    """Write a diagnostic smoke manifest as stable JSON."""

    _write_json(path, manifest.model_dump(mode="json"))


def write_phase13_diagnostic_provenance_report_json(
    path: str | Path,
    report: Phase13DiagnosticProvenanceReport,
) -> None:
    """Write a diagnostic provenance report as stable JSON."""

    _write_json(path, report.model_dump(mode="json"))


def render_phase13_diagnostic_smoke_manifest_markdown(
    manifest: Phase13DiagnosticSmokeManifest,
) -> str:
    """Render diagnostic smoke manifest Markdown."""

    lines = [
        "# Phase 13 Diagnostic Smoke Manifest",
        "",
        "## Diagnostic Scope",
        "",
        f"- Diagnostic only: `{str(manifest.diagnostic_only).lower()}`",
        f"- Claim status: `{manifest.claim_status}`",
        f"- {PHASE13_DIAGNOSTIC_SCOPE_SENTENCE}.",
        f"- Asset count: `{manifest.asset_count}`",
        f"- Short window: `{manifest.short_window_start}` to `{manifest.short_window_end}`",
        "",
        "## Forbidden Claims",
        "",
        *_bullet_lines(manifest.forbidden_claims),
        "",
        "## Source Provenance",
        "",
        f"- Data source: `{manifest.data_source}`",
        f"- Downgrade status: `{manifest.source_evidence.downgrade_status}`",
        f"- BaoStock blend status: `{manifest.source_evidence.baostock_blend_status}`",
        "",
        "## Direct Qlib Blocker",
        "",
        f"- {manifest.source_evidence.direct_qlib_blocker or 'None recorded.'}",
        "",
        "## Paper-Target Gap",
        "",
        f"- {manifest.source_evidence.paper_target_gap}",
        "",
        "## Decision Evidence",
        "",
        *_bullet_lines(manifest.decision_ids),
        "",
    ]
    return "\n".join(lines)


def render_phase13_diagnostic_provenance_report_markdown(
    report: Phase13DiagnosticProvenanceReport,
) -> str:
    """Render diagnostic provenance report Markdown."""

    source = report.source_evidence
    lines = [
        "# Phase 13 Diagnostic Provenance Report",
        "",
        "## Diagnostic Scope",
        "",
        f"- Diagnostic only: `{str(report.diagnostic_only).lower()}`",
        f"- Claim status: `{report.claim_status}`",
        f"- {PHASE13_DIAGNOSTIC_SCOPE_SENTENCE}.",
        "",
        "## Forbidden Claims",
        "",
        *_bullet_lines(report.forbidden_claims),
        "",
        "## Source Provenance",
        "",
        f"- Data source: `{source.data_source}`",
        f"- Downgrade status: `{source.downgrade_status}`",
        f"- HF source revision: `{source.hf_source_revision or 'not_used'}`",
        f"- HF coverage summary: `{_compact_json(source.hf_coverage_summary)}`",
        f"- BaoStock blend status: `{source.baostock_blend_status}`",
        "",
        "## Direct Qlib Blocker",
        "",
        f"- {report.direct_qlib_blocker}",
        "",
        "## Paper-Target Gap",
        "",
        f"- {report.paper_target_gap}",
        "",
        "## Decision Evidence",
        "",
        *_bullet_lines(report.decision_ids),
        "",
    ]
    return "\n".join(lines)


def write_phase13_diagnostic_smoke_manifest_markdown(
    path: str | Path,
    manifest: Phase13DiagnosticSmokeManifest,
) -> None:
    """Write diagnostic smoke manifest Markdown."""

    _write_text(path, render_phase13_diagnostic_smoke_manifest_markdown(manifest))


def write_phase13_diagnostic_provenance_report_markdown(
    path: str | Path,
    report: Phase13DiagnosticProvenanceReport,
) -> None:
    """Write diagnostic provenance report Markdown."""

    _write_text(path, render_phase13_diagnostic_provenance_report_markdown(report))


def _require_diagnostic_only(diagnostic_only: bool, claim_status: str) -> None:
    if diagnostic_only is not True:
        raise ValueError("diagnostic smoke artifacts require diagnostic_only=true")
    if claim_status != "diagnostic_only":
        raise ValueError("diagnostic smoke artifacts require claim_status=diagnostic_only")


def _require_items(name: str, observed: list[str], required: tuple[str, ...]) -> None:
    missing = sorted(set(required) - set(observed))
    if missing:
        raise ValueError(f"{name} missing required items: {missing}")


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_text(path: str | Path, text: str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


def _bullet_lines(items: list[str]) -> list[str]:
    if not items:
        return ["- None recorded."]
    return [f"- {item}" for item in items]


def _compact_json(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return "not_used"
    return json.dumps(payload, sort_keys=True)
