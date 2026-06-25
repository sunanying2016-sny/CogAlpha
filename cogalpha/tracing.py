"""Trace event schemas and JSONL IO for auditable CogAlpha runs."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TraceEventKind(StrEnum):
    """Event kinds emitted by trace-first CogAlpha execution."""

    RUN_STARTED = "run_started"
    AGENT_DECISION = "agent_decision"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_FINISHED = "tool_call_finished"
    SKILL_INVOCATION_STARTED = "skill_invocation_started"
    SKILL_INVOCATION_FINISHED = "skill_invocation_finished"
    GUARD_REPORT_RECORDED = "guard_report_recorded"
    CANDIDATE_STAGE_CHANGED = "candidate_stage_changed"
    FITNESS_EVALUATION_RECORDED = "fitness_evaluation_recorded"
    STOP_DECISION = "stop_decision"
    RUN_FINISHED = "run_finished"


class CogAlphaTraceEvent(BaseModel):
    """One append-only trace event for a CogAlpha run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=0)
    kind: TraceEventKind
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


class TraceLedger:
    """Append trace events to JSONL with monotonic sequence numbers."""

    def __init__(self, *, run_id: str, path: str | Path, start_sequence: int = 0) -> None:
        if not run_id:
            msg = "run_id must be non-empty"
            raise ValueError(msg)
        self.run_id = run_id
        self.path = Path(path)
        self._next_sequence = start_sequence
        self._lock = threading.Lock()

    def record(
        self,
        kind: TraceEventKind | str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> CogAlphaTraceEvent:
        """Create and append one trace event.

        The sequence read, file append, and counter increment are guarded by a
        single lock so concurrent ``record`` calls produce monotonic, unique,
        gap-free sequence numbers with no torn appends (CONC-04).
        """

        with self._lock:
            event = CogAlphaTraceEvent(
                run_id=self.run_id,
                sequence=self._next_sequence,
                kind=TraceEventKind(kind),
                payload=payload or {},
            )
            append_trace_event(self.path, event)
            self._next_sequence += 1
        return event


def append_trace_event(path: str | Path, event: CogAlphaTraceEvent) -> None:
    """Append one trace event to a JSONL file."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(_event_to_json(event))
        handle.write("\n")


def write_trace_events(path: str | Path, events: Iterable[CogAlphaTraceEvent]) -> None:
    """Write trace events as stable JSONL, preserving iterable order."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(_event_to_json(event))
            handle.write("\n")


def read_trace_events(path: str | Path) -> list[CogAlphaTraceEvent]:
    """Read trace events from JSONL in file order."""

    input_path = Path(path)
    events: list[CogAlphaTraceEvent] = []
    with input_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            events.append(CogAlphaTraceEvent.model_validate_json(line))
    return events


def _event_to_json(event: CogAlphaTraceEvent) -> str:
    return json.dumps(event.model_dump(mode="json"), sort_keys=True)
