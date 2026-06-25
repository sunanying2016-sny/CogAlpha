"""Engine-level concurrency-safe artifact writing (CONC-05).

This module owns the engine's append-only artifact surface:

* :class:`ModelIORecorder` — a ``SkillInvoker``-compatible wrapper that persists redacted
  prompt/output evidence (sha256 fingerprints + path/secret redaction + env-only secrets).
  Re-homed verbatim from the former ``validation.evidence`` recorder so the engine emits
  redacted model I/O without importing the ``validation`` package, which is quarantined off
  the engine import path in Phase 21 (O-5 / CLEAN-03). No compatibility re-export is left
  behind in ``validation/`` (D-03a, no shim).

Shared redaction / JSONL helpers live in the neutral :mod:`cogalpha.redaction` module so
both this engine module and the residual ``validation/evidence.py`` consumers import them
without a ``validation -> artifacts`` reverse dependency (Open Q1 = b).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from cogalpha.redaction import (
    _append_jsonl,
    _redact_secret_values,
    _safe_name,
    _sha256,
)
from cogalpha.skill_runtime.io import parse_skill_model_output
from cogalpha.state import CogAlphaState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cogalpha.protocol import ProtocolConfig

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class FsyncPolicy(StrEnum):
    """When the :class:`ArtifactWriter` forces buffered appends to durable storage.

    This is a run / orchestration configuration value; it deliberately does NOT live in the
    frozen ``ProtocolConfig`` (paper system variables only — Pitfall 6).

    * ``PER_GENERATION`` (default) — appends are buffered; the orchestrator calls
      :meth:`ArtifactWriter.flush_and_fsync` once at each generation/checkpoint boundary
      (before the durable checkpoint write — Pitfall 4 order) to fsync all three streams.
    * ``PER_RECORD`` — every append is fsynced immediately (maximum durability, slower).
    * ``NEVER`` — never fsync (OS flushes on its own schedule; fastest, least durable).
    """

    PER_GENERATION = "per_generation"
    PER_RECORD = "per_record"
    NEVER = "never"


class _Stream:
    """One append-only JSONL stream with its own lock and monotonic sequence."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()
        self.sequence = 0


def _fsync_dir(directory: Path) -> None:
    """fsync a directory fd so a file creation / ``os.replace`` rename is itself durable.

    WR-01: on POSIX a freshly created file's directory entry (and the rename of
    ``gen-<g>.json.tmp`` -> ``gen-<g>.json``) is not guaranteed durable until the
    *containing directory* is fsynced -- otherwise a crash can lose the file even
    after its contents were fsynced, defeating the Pitfall-4 ordering guarantee.
    Best-effort: platforms that cannot open/fsync a directory (e.g. Windows) are
    skipped rather than failing the write.
    """

    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


class ArtifactWriter:
    """Engine-level concurrency-safe append-only writer for the three artifact streams.

    Serves the ``trace`` / ``skill_invocations`` / ``model_io`` (redacted evidence) JSONL
    streams. Each stream has its **own** ``threading.Lock`` and independent sequence space; a
    lock guards only the fast critical section — sequence increment, file append, and the
    in-memory record list — never any slow call (no LLM / exec inside the writer). Records are
    written with deterministic ``sort_keys`` ordering (D-03).

    This is a single-writer-per-stream design (no second competing trace writer — STATE-03
    single-event model). ``DurableCheckpointWriter`` and the orchestrator-side fsync wiring at
    the checkpoint boundary are added by Wave 3 / 20-05, which calls :meth:`flush_and_fsync`.
    """

    def __init__(
        self,
        *,
        trace_path: str | Path,
        skill_invocations_path: str | Path,
        model_io_path: str | Path,
        fsync_policy: FsyncPolicy = FsyncPolicy.PER_GENERATION,
    ) -> None:
        self.fsync_policy = fsync_policy
        self._trace = _Stream(trace_path)
        self._skill_invocations = _Stream(skill_invocations_path)
        self._model_io = _Stream(model_io_path)
        self._streams: tuple[_Stream, ...] = (
            self._trace,
            self._skill_invocations,
            self._model_io,
        )

    def append_trace(self, payload: dict[str, Any]) -> int:
        """Append one record to the trace stream; returns its assigned sequence."""

        return self._append(self._trace, payload)

    def append_skill_invocation(self, payload: dict[str, Any]) -> int:
        """Append one record to the skill-invocation stream; returns its sequence."""

        return self._append(self._skill_invocations, payload)

    def append_model_io(self, payload: dict[str, Any]) -> int:
        """Append one record to the redacted model-I/O stream; returns its sequence."""

        return self._append(self._model_io, payload)

    def flush_and_fsync(self) -> None:
        """Force all three streams to durable storage in one call.

        For ``PER_GENERATION`` (default) the orchestrator calls this at the checkpoint
        boundary BEFORE writing the durable checkpoint (Pitfall 4 order). ``NEVER`` makes
        this a no-op; ``PER_RECORD`` already fsynced each append but re-fsyncing here is safe.
        """

        if self.fsync_policy is FsyncPolicy.NEVER:
            return
        for stream in self._streams:
            with stream.lock:
                self._fsync_path(stream.path)

    def _append(self, stream: _Stream, payload: dict[str, Any]) -> int:
        # The lock guards ONLY the fast critical section (sequence++ + file append) so
        # concurrent producers never interleave half-lines or duplicate a sequence. No slow
        # call (LLM / exec) is ever made inside the writer, so this lock is never hot.
        with stream.lock:
            sequence = stream.sequence
            stream.sequence += 1
            record = {**payload, "sequence": sequence}
            stream.path.parent.mkdir(parents=True, exist_ok=True)
            with stream.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True))
                handle.write("\n")
                if self.fsync_policy is FsyncPolicy.PER_RECORD:
                    handle.flush()
                    self._fsync(handle)
        return sequence

    def _fsync_path(self, path: Path) -> None:
        if not path.is_file():
            return
        with path.open("a", encoding="utf-8") as handle:
            handle.flush()
            self._fsync(handle)
        # WR-01: also fsync the containing directory so the stream file's directory
        # entry is durable (fsyncing the contents alone is not crash-safe on POSIX).
        _fsync_dir(path.parent)

    def _fsync(self, handle: IO[Any]) -> None:
        # Isolated so the durability primitive is overridable/observable (tests assert call
        # counts) without re-entering the lock-guarded append path.
        os.fsync(handle.fileno())


class ModelIOEvidenceRecord(BaseModel):
    """One sanitized prompt/output evidence pointer."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(..., ge=0)
    skill_name: str = Field(..., min_length=1)
    schema_name: str = Field(..., min_length=1)
    request_sha256: str = Field(..., min_length=1)
    prompt_sha256: str = Field(..., min_length=1)
    output_sha256: str = Field(..., min_length=1)
    prompt_path: str = Field(..., min_length=1)
    output_path: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)
    candidate_ids: list[str] = Field(default_factory=list)
    model_settings: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


@dataclass
class ModelIORecorder:
    """SkillInvoker-compatible wrapper that persists redacted prompt/output evidence.

    Thread-safe: the sequence counter, per-call prompt/output file writes, the
    evidence JSONL append, and the in-memory ``calls`` list are all guarded by a
    single lock so concurrent skill invocations (bounded ThreadPoolExecutor
    dispatch) produce non-interleaved, uniquely-sequenced records.

    Re-homed from the former ``validation.evidence`` recorder with the engine-neutral name;
    the ``inner`` / ``output_dir`` / ``evidence_jsonl`` / ``_lock`` structure and the
    ``invoke`` / ``_write_record`` logic are unchanged.
    """

    inner: Any
    output_dir: str | Path
    evidence_jsonl: str | Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        self.model_io_dir = self.output_dir / "model_io"
        self.evidence_jsonl = (
            Path(self.evidence_jsonl)
            if self.evidence_jsonl is not None
            else self.model_io_dir / "evidence.jsonl"
        )
        self.calls: list[dict[str, Any]] = []
        self._sequence = 0

    def invoke(
        self,
        skill_name: str,
        request: BaseModel,
        output_schema: type[SchemaT],
    ) -> SchemaT:
        """Invoke a skill while recording sanitized prompt/output artifacts."""

        context = self.inner.prepare_context(skill_name, request, output_schema)
        prompt_text = _redact_secret_values(context.prompt)
        output_text = ""
        status = "ok"
        error = None
        result: SchemaT | None = None
        try:
            raw_output = self.inner.client.complete_text(context.prompt)
            output_text = _redact_secret_values(raw_output)
            parsed = parse_skill_model_output(
                skill_name=skill_name,
                model_output=raw_output,
                request=request,
                artifact_schema=output_schema,
            )
            result = output_schema.model_validate(parsed)
            return result
        except Exception as exc:
            status = "error"
            error = str(exc)
            raise
        finally:
            # Guard only the record-writing block (sequence counter, file writes,
            # JSONL append, calls list) — NOT the LLM call above — so concurrency
            # parallelizes I/O-bound model calls while keeping evidence consistent.
            with self._lock:
                record = self._write_record(
                    skill_name=skill_name,
                    request=request,
                    schema_name=output_schema.__name__,
                    prompt_text=prompt_text,
                    output_text=output_text,
                    status=status,
                    candidate_ids=_extract_candidate_ids(result),
                    model_settings=_model_settings_from_context(context.metadata),
                    error=error,
                )
                self.calls.append(record.model_dump(mode="json"))

    def _write_record(
        self,
        *,
        skill_name: str,
        request: BaseModel,
        schema_name: str,
        prompt_text: str,
        output_text: str,
        status: str,
        candidate_ids: list[str],
        model_settings: dict[str, str],
        error: str | None,
    ) -> ModelIOEvidenceRecord:
        sequence = self._sequence
        self._sequence += 1
        safe_skill_name = _safe_name(skill_name)
        prompt_path = self.model_io_dir / f"{sequence:04d}-{safe_skill_name}-prompt.txt"
        output_path = self.model_io_dir / f"{sequence:04d}-{safe_skill_name}-output.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt_text, encoding="utf-8")
        output_path.write_text(output_text, encoding="utf-8")
        record = ModelIOEvidenceRecord(
            sequence=sequence,
            skill_name=skill_name,
            schema_name=schema_name,
            request_sha256=_sha256(request.model_dump_json(exclude_none=True)),
            prompt_sha256=_sha256(prompt_text),
            output_sha256=_sha256(output_text),
            prompt_path=str(prompt_path),
            output_path=str(output_path),
            status=status,
            candidate_ids=candidate_ids,
            model_settings=model_settings,
            error=_redact_secret_values(error) if error else None,
        )
        assert self.evidence_jsonl is not None  # set in __post_init__
        _append_jsonl(self.evidence_jsonl, record.model_dump(mode="json"))
        return record


def _model_settings_from_context(metadata: dict[str, Any]) -> dict[str, str]:
    settings: dict[str, str] = {}
    for key in ("model", "effort", "context"):
        value = metadata.get(key)
        if value:
            settings[key] = _redact_secret_values(str(value))
    return settings


def _extract_candidate_ids(result: BaseModel | None) -> list[str]:
    if result is None:
        return []
    ids: list[str] = []
    candidates = getattr(result, "candidates", None)
    if candidates:
        ids.extend(str(candidate.candidate_id) for candidate in candidates)
    candidate_id = getattr(result, "candidate_id", None)
    if candidate_id:
        ids.append(str(candidate_id))
    candidate = getattr(result, "candidate", None)
    if candidate is not None and getattr(candidate, "candidate_id", None):
        ids.append(str(candidate.candidate_id))
    repaired = getattr(result, "repaired_candidate", None)
    if repaired is not None and getattr(repaired, "candidate_id", None):
        ids.append(str(repaired.candidate_id))
    return sorted(set(ids))


# --- CONC-06: durable per-generation checkpoint / resume -----------------------


class _FlushableArtifactWriter(Protocol):
    """Minimal structural view of the part of ``ArtifactWriter`` the checkpoint
    boundary consumes: a single ``flush_and_fsync()`` call that durably persists
    every artifact stream (Pitfall 4 — fsync artifacts BEFORE the checkpoint)."""

    def flush_and_fsync(self) -> None: ...


class DurableCheckpointWriter:
    """Durable per-generation checkpoint writer (CONC-06; implements the Phase-18
    :class:`~cogalpha.stages.CheckpointWriter` Protocol — ``write(state, generation)``).

    Each completed generation is snapshotted to ``checkpoints/gen-<g>.json`` via an
    atomic temp-file + :func:`os.replace` write: a crash mid-write never corrupts the
    latest checkpoint (D-02b). Every snapshot is kept; ``latest = max(g)``.

    When an ``artifact_writer`` is injected, :meth:`write` flushes + fsyncs all three
    artifact streams BEFORE writing the checkpoint (Pitfall 4 / D-03b ordering) so the
    latest checkpoint always corresponds to durably persisted artifacts.
    """

    def __init__(
        self,
        checkpoint_dir: str | Path,
        *,
        run_identity: dict[str, Any],
        artifact_writer: _FlushableArtifactWriter | None = None,
    ) -> None:
        self._dir = Path(checkpoint_dir)
        self._identity = run_identity
        self._artifact_writer = artifact_writer

    def write(self, state: CogAlphaState, generation: int) -> None:
        # Pitfall 4: durably persist the artifact streams FIRST, so the checkpoint
        # we are about to write can never point at not-yet-flushed artifacts.
        if self._artifact_writer is not None:
            self._artifact_writer.flush_and_fsync()

        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "generation": generation,
            "identity": self._identity,
            "state": json.loads(state.model_dump_json()),
        }
        target = self._dir / f"gen-{generation}.json"
        tmp = target.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        # POSIX atomic rename: a crash leaves either the old target or the new
        # complete one — never a half-written file (D-02b).
        os.replace(tmp, target)
        # WR-01: fsync the checkpoint directory so the rename itself is durable —
        # otherwise a crash can lose the renamed gen-<g>.json even though its
        # contents were fsynced, defeating the Pitfall-4 ordering guarantee.
        _fsync_dir(self._dir)


def latest_checkpoint(checkpoint_dir: str | Path) -> tuple[int, CogAlphaState] | None:
    """Load the latest (``max(g)``) ``gen-<g>.json`` checkpoint, if any.

    Returns ``(generation, state)`` with the snapshot re-validated back into a typed
    :class:`~cogalpha.state.CogAlphaState`, or ``None`` when no checkpoint exists.
    """

    directory = Path(checkpoint_dir)
    paths = list(directory.glob("gen-*.json"))
    if not paths:
        return None
    g = max(int(path.stem.split("-")[1]) for path in paths)
    payload = json.loads((directory / f"gen-{g}.json").read_text(encoding="utf-8"))
    return g, CogAlphaState.model_validate(payload["state"])


def build_run_identity(protocol: ProtocolConfig) -> dict[str, Any]:
    """Build the minimal run identity embedded in each checkpoint (Pitfall 5).

    Carries a sha256 over the protocol's structural / cadence fields plus the
    benchmark preset id, so :func:`validate_checkpoint_identity` can refuse to resume
    a checkpoint produced under a different ProtocolConfig / universe.
    """

    preset_id = protocol.benchmark_spec.preset_id
    key_fields = {
        "generations": protocol.generations,
        "inner_subcycles": protocol.inner_subcycles,
        "subcycle_length": protocol.subcycle_length,
        "domain_agents": protocol.domain_agents,
        "initial_pool": protocol.initial_pool,
        "parent_pool": protocol.parent_pool,
        "children_pool": protocol.children_pool,
        "preset_id": preset_id,
    }
    digest = hashlib.sha256(
        json.dumps(key_fields, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {"protocol_sha256": digest, "preset_id": preset_id}


def validate_checkpoint_identity(
    payload_identity: dict[str, Any],
    current_identity: dict[str, Any],
) -> None:
    """Refuse to resume a checkpoint whose run identity does not match the current
    ProtocolConfig / universe (Pitfall 5 — no silently-wrong resume)."""

    if payload_identity != current_identity:
        raise ValueError(
            "checkpoint run identity does not match the current protocol/universe; "
            "refusing to resume (re-run from scratch or restore the matching config). "
            f"checkpoint={payload_identity} current={current_identity}"
        )
