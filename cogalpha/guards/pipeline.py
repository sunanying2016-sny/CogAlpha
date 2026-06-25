"""Deterministic Guard sequencing for Alpha Candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from cogalpha.guards.alpha_code import run_static_alpha_code_guard
from cogalpha.guards.alpha_runtime import run_runtime_alpha_code_guard
from cogalpha.schemas import AlphaCandidate, GuardReport, GuardStatus


@dataclass(frozen=True)
class DeterministicGuardOutcome:
    """All Deterministic Guard reports for one Alpha Candidate check."""

    reports: list[GuardReport]

    @property
    def failed(self) -> bool:
        """Return whether any Deterministic Guard failed."""

        return any(report.status == GuardStatus.FAIL for report in self.reports)

    def to_evidence(self, candidate_id: str) -> dict[str, Any]:
        """Return JSON-safe guard evidence for validation packages."""

        evidence = {
            "candidate_id": candidate_id,
            "reports": [
                {
                    "guard_name": report.guard_name,
                    "status": report.status.value,
                    "issue_codes": [issue.code for issue in report.issues],
                    "candidate_id": str(report.metadata.get("candidate_id", candidate_id)),
                    "metadata": report.metadata,
                }
                for report in self.reports
            ],
        }
        return json.loads(json.dumps(evidence, sort_keys=True, default=str))


@dataclass(frozen=True)
class DeterministicGuardPipeline:
    """Run the deterministic guard path for an Alpha Candidate.

    Runtime numerical checks are enabled when an OHLCV Input sample is supplied.
    Without a sample, this preserves the MVP Loop's static-only behavior.
    """

    runtime_ohlcv_panel: pd.DataFrame | None = None
    max_nan_fraction: float = 0.30
    timeout_seconds: float | None = None

    def run(self, candidate: AlphaCandidate) -> DeterministicGuardOutcome:
        """Run static checks, then runtime checks when static checks pass."""

        reports = [run_static_alpha_code_guard(candidate.alpha.code, candidate.alpha.name)]
        if reports[-1].status == GuardStatus.FAIL:
            return DeterministicGuardOutcome(reports=reports)

        if self.runtime_ohlcv_panel is not None:
            reports.append(
                run_runtime_alpha_code_guard(
                    candidate,
                    self.runtime_ohlcv_panel,
                    max_nan_fraction=self.max_nan_fraction,
                    timeout_seconds=self.timeout_seconds,
                )
            )
        return DeterministicGuardOutcome(reports=reports)
