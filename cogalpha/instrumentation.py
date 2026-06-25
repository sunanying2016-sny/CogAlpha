"""Instrumentation helpers for formal CogAlpha workflow runs."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from cogalpha.skill_runtime.nodes import StructuredArtifactInvoker

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass
class InvocationRecorder:
    """Append skill invocation records to JSONL.

    File appends are guarded by a lock so concurrent skill invocations (bounded
    ThreadPoolExecutor dispatch) cannot interleave partial JSON lines.
    """

    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def append(self, record: dict) -> None:
        """Append one invocation record (thread-safe)."""

        payload = json.dumps(record, sort_keys=True) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload)


@dataclass
class RecordingInvoker:
    """StructuredArtifactInvoker wrapper that records latency and errors.

    The in-memory ``calls`` list is appended under a lock so concurrent invokes
    do not corrupt it. Ordering of ``calls`` follows wall-clock completion under
    concurrency; result ORDER for the run is preserved by the caller (each node
    collects parallel results back into the original sequential index order).
    """

    inner: StructuredArtifactInvoker
    recorder: InvocationRecorder
    context_variant: str
    calls: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def invoke(
        self,
        skill_name: str,
        request: BaseModel,
        output_schema: type[SchemaT],
    ) -> SchemaT:
        """Invoke a skill and record public instrumentation."""

        started = time.perf_counter()
        record = {
            "created_at": datetime.now(UTC).isoformat(),
            "skill_name": skill_name,
            "schema_name": output_schema.__name__,
            "context_variant": self.context_variant,
            "request_sha256": hashlib.sha256(
                request.model_dump_json(exclude_none=True).encode("utf-8")
            ).hexdigest(),
            "status": "ok",
        }
        try:
            result = self.inner.invoke(skill_name, request, output_schema)
        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            raise
        finally:
            record["latency_seconds"] = time.perf_counter() - started
            self.recorder.append(record)
            with self._lock:
                self.calls.append(record)
        return result
