"""Semantic trace verification for CogAlpha final states."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cogalpha.schemas import CandidateStage, CogAlphaState
from cogalpha.tracing import CogAlphaTraceEvent, TraceEventKind

SUCCESSFUL_FITNESS_STATUSES = {"ok", "success"}
REJECTED_FITNESS_STATUSES = {"ok", "success", "rejected"}
QUALITY_ACCEPTANCE_STATUSES = {"pass", "passed", "ok", "success", "accept", "accepted"}
QUALITY_REJECTION_STATUSES = {"fail", "failed", "error", "reject", "rejected"}
REQUIRED_FITNESS_METRICS = {"ic", "rank_ic", "icir", "rank_icir", "mi"}


class TraceVerificationFinding(BaseModel):
    """One semantic trace verification finding."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1)
    severity: Literal["error", "warning"] = "error"
    message: str = Field(..., min_length=1)


class TraceVerificationReport(BaseModel):
    """Aggregate verification report for one final state and trace."""

    model_config = ConfigDict(extra="forbid")

    findings: list[TraceVerificationFinding] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Return true only when no error-severity findings exist."""

        return not any(finding.severity == "error" for finding in self.findings)


def verify_cogalpha_trace(
    final_state: CogAlphaState | dict[str, Any],
    events: Iterable[CogAlphaTraceEvent | dict[str, Any]],
) -> TraceVerificationReport:
    """Verify trace events semantically support a CogAlpha final state."""

    state = CogAlphaState.model_validate(final_state)
    trace_events = [CogAlphaTraceEvent.model_validate(event) for event in events]
    findings: list[TraceVerificationFinding] = []

    tool_results = _successful_tool_results(trace_events)
    for node_name in [result.node_name for result in state.node_history]:
        if not _has_tool_result(node_name, tool_results):
            findings.append(
                _error(
                    "missing_tool_result",
                    f"Node history entry {node_name!r} has no matching successful tool result.",
                )
            )

    provenance_candidate_ids = _candidate_ids_for_kinds(
        trace_events,
        {TraceEventKind.CANDIDATE_STAGE_CHANGED},
    )
    quality_evidence = _quality_evidence_by_candidate(trace_events)
    fitness_evidence = _fitness_evidence_by_candidate(trace_events)
    quality_rejection_evidence = _quality_rejection_evidence_by_candidate(trace_events)

    for candidate_id in _final_candidate_ids(state):
        if candidate_id not in provenance_candidate_ids:
            findings.append(
                _error(
                    "missing_candidate_provenance",
                    f"Final candidate {candidate_id!r} has no generation or evolution trace event.",
                )
            )

    for candidate_id in _accepted_candidate_ids(state):
        evidence = quality_evidence.get(candidate_id, [])
        if not evidence or not any(_has_quality_acceptance_status(event) for event in evidence):
            findings.append(
                _error(
                    "quality_acceptance_status_mismatch",
                    "Accepted candidate "
                    f"{candidate_id!r} requires a passed/accepted quality trace status.",
                )
            )

    for candidate_id in _rejected_by_quality_candidate_ids(state):
        evidence = quality_rejection_evidence.get(candidate_id, [])
        if not evidence or not any(_has_quality_rejection_status(event) for event in evidence):
            statuses = [
                str(event.payload.get("status"))
                for event in evidence
                if event.payload.get("status") is not None
            ]
            findings.append(
                _error(
                    "quality_rejection_status_mismatch",
                    "Quality-rejected candidate "
                    f"{candidate_id!r} requires a failed/rejected quality trace status "
                    f"but trace recorded {statuses or ['<missing>']!r}.",
                )
            )

    for candidate_id, expected_stage in _evaluated_final_candidate_stages(state).items():
        candidate_evidence = fitness_evidence.get(candidate_id, [])
        if not candidate_evidence:
            findings.append(
                _error(
                    "missing_fitness_evaluation",
                    "Evaluated final-pool candidate "
                    f"{candidate_id!r} has no fitness evaluation trace event.",
                )
            )
            continue
        findings.extend(
            _verify_fitness_evidence(
                candidate_id,
                expected_stage,
                candidate_evidence,
            )
        )

    if not _has_stop_reason(trace_events):
        findings.append(
            _error(
                "missing_stop_reason",
                "Trace has no stop_decision event with a non-empty reason.",
            )
        )

    return TraceVerificationReport(findings=findings)


def _successful_tool_results(events: list[CogAlphaTraceEvent]) -> set[str]:
    tool_names: set[str] = set()
    for event in events:
        if event.kind != TraceEventKind.TOOL_CALL_FINISHED:
            continue
        status = event.payload.get("status")
        if status not in {"ok", "success"}:
            continue
        tool_name = event.payload.get("tool_name") or event.payload.get("tool")
        if isinstance(tool_name, str) and tool_name:
            tool_names.add(tool_name)
    return tool_names


def _has_tool_result(node_name: str, tool_results: set[str]) -> bool:
    aliases = {
        "domain_agents": {"domain_agents", "domain_agents.generate"},
        "quality_pipeline": {"quality_pipeline", "quality_pipeline.review"},
        "fitness_gate": {"fitness_gate", "fitness_gate.evaluate"},
        "thinking_evolution": {
            "thinking_evolution",
            "thinking_evolution.generate_children",
        },
    }.get(node_name, {node_name})
    return any(tool_name in aliases for tool_name in tool_results)


def _candidate_ids_for_kinds(
    events: list[CogAlphaTraceEvent],
    kinds: set[TraceEventKind],
) -> set[str]:
    candidate_ids: set[str] = set()
    for event in events:
        if event.kind not in kinds:
            continue
        candidate_id = event.payload.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            candidate_ids.add(candidate_id)
        for candidate_id in event.payload.get("candidate_ids", []):
            if isinstance(candidate_id, str) and candidate_id:
                candidate_ids.add(candidate_id)
    return candidate_ids


def _fitness_evidence_by_candidate(
    events: list[CogAlphaTraceEvent],
) -> dict[str, list[CogAlphaTraceEvent]]:
    evidence: dict[str, list[CogAlphaTraceEvent]] = {}
    for event in events:
        if event.kind != TraceEventKind.FITNESS_EVALUATION_RECORDED:
            continue
        candidate_id = event.payload.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            evidence.setdefault(candidate_id, []).append(event)
    return evidence


def _quality_evidence_by_candidate(
    events: list[CogAlphaTraceEvent],
) -> dict[str, list[CogAlphaTraceEvent]]:
    evidence: dict[str, list[CogAlphaTraceEvent]] = {}
    for event in events:
        if event.kind not in {
            TraceEventKind.GUARD_REPORT_RECORDED,
            TraceEventKind.SKILL_INVOCATION_FINISHED,
        }:
            continue
        candidate_id = event.payload.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            evidence.setdefault(candidate_id, []).append(event)
    return evidence


def _quality_rejection_evidence_by_candidate(
    events: list[CogAlphaTraceEvent],
) -> dict[str, list[CogAlphaTraceEvent]]:
    evidence: dict[str, list[CogAlphaTraceEvent]] = {}
    for event in events:
        if event.kind != TraceEventKind.GUARD_REPORT_RECORDED:
            continue
        candidate_id = event.payload.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            evidence.setdefault(candidate_id, []).append(event)
    return evidence


def _has_quality_acceptance_status(event: CogAlphaTraceEvent) -> bool:
    return event.payload.get("status") in QUALITY_ACCEPTANCE_STATUSES


def _has_quality_rejection_status(event: CogAlphaTraceEvent) -> bool:
    return event.payload.get("status") in QUALITY_REJECTION_STATUSES


def _verify_fitness_evidence(
    candidate_id: str,
    expected_stage: CandidateStage,
    evidence: list[CogAlphaTraceEvent],
) -> list[TraceVerificationFinding]:
    findings: list[TraceVerificationFinding] = []

    if any(_supports_expected_fitness(event, expected_stage) for event in evidence):
        return findings

    matching_stage_events = [
        event for event in evidence if _payload_stage(event) == expected_stage
    ]
    acceptable_status_events = [
        event
        for event in evidence
        if _has_acceptable_fitness_status(expected_stage, event.payload.get("status"))
    ]
    matching_stage_acceptable_status_events = [
        event
        for event in matching_stage_events
        if _has_acceptable_fitness_status(expected_stage, event.payload.get("status"))
    ]

    if not matching_stage_acceptable_status_events:
        findings.append(
            _error(
                "failed_fitness_evaluation",
                "Evaluated final-pool candidate "
                f"{candidate_id!r} has no successful fitness evaluation status.",
            )
        )

    if expected_stage in {CandidateStage.QUALIFIED, CandidateStage.ELITE} and (
        not matching_stage_acceptable_status_events
        or not any(
            _has_required_fitness_metrics(event)
            for event in matching_stage_acceptable_status_events
        )
    ):
        findings.append(
            _error(
                "missing_fitness_metrics",
                "Evaluated final-pool candidate "
                f"{candidate_id!r} has no complete fitness metrics evidence.",
            )
        )

    if not matching_stage_events or not any(
        _payload_stage(event) == expected_stage for event in acceptable_status_events
    ):
        stages = [_payload_stage(event) for event in evidence]
        stage_values = sorted(stage.value for stage in stages if stage is not None)
        findings.append(
            _error(
                "fitness_stage_mismatch",
                "Evaluated final-pool candidate "
                f"{candidate_id!r} expected fitness stage {expected_stage.value!r} "
                f"but trace recorded {stage_values or ['<missing>']!r}.",
            )
        )

    return findings


def _supports_expected_fitness(
    event: CogAlphaTraceEvent,
    expected_stage: CandidateStage,
) -> bool:
    if _payload_stage(event) != expected_stage:
        return False
    if not _has_acceptable_fitness_status(expected_stage, event.payload.get("status")):
        return False
    if expected_stage in {CandidateStage.QUALIFIED, CandidateStage.ELITE}:
        return _has_required_fitness_metrics(event)
    return True


def _has_acceptable_fitness_status(
    expected_stage: CandidateStage,
    status: object,
) -> bool:
    allowed_statuses = (
        REJECTED_FITNESS_STATUSES
        if expected_stage == CandidateStage.REJECTED_BY_FITNESS
        else SUCCESSFUL_FITNESS_STATUSES
    )
    return status in allowed_statuses


def _payload_stage(event: CogAlphaTraceEvent) -> CandidateStage | None:
    stage = event.payload.get("stage")
    if isinstance(stage, CandidateStage):
        return stage
    if isinstance(stage, str):
        try:
            return CandidateStage(stage)
        except ValueError:
            return None
    return None


def _has_required_fitness_metrics(event: CogAlphaTraceEvent) -> bool:
    metrics = event.payload.get("metrics")
    return isinstance(metrics, dict) and REQUIRED_FITNESS_METRICS.issubset(metrics)


def _final_candidate_ids(state: CogAlphaState) -> set[str]:
    return {
        candidate.candidate_id
        for candidate in [
            *state.candidates,
            *state.qualified_pool,
            *state.elite_pool,
            *state.rejected_pool,
        ]
    }


def _accepted_candidate_ids(state: CogAlphaState) -> set[str]:
    accepted_stages = {
        CandidateStage.ACCEPTED_BY_QUALITY,
        CandidateStage.QUALIFIED,
        CandidateStage.ELITE,
    }
    return {
        candidate.candidate_id
        for candidate in [
            *state.candidates,
            *state.qualified_pool,
            *state.elite_pool,
        ]
        if candidate.stage in accepted_stages
    }


def _rejected_by_quality_candidate_ids(state: CogAlphaState) -> set[str]:
    return {
        candidate.candidate_id
        for candidate in state.rejected_pool
        if candidate.stage == CandidateStage.REJECTED_BY_QUALITY
    }


def _evaluated_final_candidate_stages(state: CogAlphaState) -> dict[str, CandidateStage]:
    evaluated_stages = {
        CandidateStage.QUALIFIED,
        CandidateStage.ELITE,
        CandidateStage.REJECTED_BY_FITNESS,
    }
    return {
        candidate.candidate_id: candidate.stage
        for candidate in [
            *state.qualified_pool,
            *state.elite_pool,
            *state.rejected_pool,
        ]
        if candidate.stage in evaluated_stages
    }


def _has_stop_reason(events: list[CogAlphaTraceEvent]) -> bool:
    for event in events:
        if event.kind != TraceEventKind.STOP_DECISION:
            continue
        reason = event.payload.get("reason")
        if isinstance(reason, str) and reason.strip():
            return True
    return False


def _error(code: str, message: str) -> TraceVerificationFinding:
    return TraceVerificationFinding(code=code, severity="error", message=message)
