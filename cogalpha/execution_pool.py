"""Subprocess isolation service for untrusted alpha code (CONC-01).

This module is the execution substrate that contains untrusted LLM-generated
alpha code. It replaces the main-thread-only ``signal.setitimer``/``SIGALRM``
timeout in ``guards/alpha_runtime.py`` (which silently no-ops under concurrency)
with a real per-task timeout backed by an explicit ``Process.kill()`` (SIGKILL)
-- the only thing that can contain a CPU-bound ``while True`` (D-01, scheme C).

Design (locked scheme C, zero new runtime dependency):

- The trusted OHLCV panel is written ONCE into a ``multiprocessing.shared_memory``
  block (raw float64 buffer) and shared read-only across every worker. The panel
  is therefore NOT pickled per task (D-01 amortize goal). The pool owns the
  shared-memory lifecycle and ``close()`` + ``unlink()``s it on shutdown.

- Each candidate runs in its own ``multiprocessing.get_context("spawn").Process``.
  The parent does ``proc.join(timeout_seconds)`` and, if the worker is still
  alive, ``proc.kill()`` (SIGKILL) -- this is what contains ``while True``;
  ``future.result(timeout=)`` does NOT kill the worker and is deliberately not
  used here. Worker concurrency is bounded to ``resolve_max_workers(concurrency)``
  (config-sourced, no hardcoded cap -- G1) and processed in input-indexed batches
  so the collected order is deterministic.

- The restricted-exec security namespace is reconstructed INSIDE each worker via
  ``from cogalpha.execution import execute_alpha_function`` -- the namespace is a
  pure, externally-stateless function, so the sandbox does NOT weaken in the
  subprocess. Workers return only basic picklable values; the parent never
  ``eval``s the returned payload.
"""

from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from multiprocessing import shared_memory
from queue import Empty
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from cogalpha.alpha_contract import RUNTIME_OHLCV_COLUMNS, RUNTIME_PANEL_INDEX_NAMES
from cogalpha.concurrency import resolve_max_workers
from cogalpha.execution import _validate_runtime_ohlcv_panel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from cogalpha.schemas import AlphaCandidate


@dataclass(frozen=True)
class AlphaExecResult:
    """Lightweight, fully-picklable result of one candidate's isolated execution."""

    candidate_id: str
    ok: bool
    factor_values: pd.Series | None = None
    error: str | None = None
    timed_out: bool = False


@dataclass(frozen=True)
class _PanelMeta:
    """Side-channel metadata to rebuild the (date, ticker) panel from raw buffer."""

    shm_name: str
    shape: tuple[int, int]
    columns: tuple[str, ...]
    date_values: tuple[object, ...]
    ticker_values: tuple[object, ...]


# --- Worker-process globals (spawn: filled by _init_worker in the child) -------

_WORKER_PANEL: pd.DataFrame | None = None
_WORKER_SHM: shared_memory.SharedMemory | None = None


def _rebuild_panel(buffer: np.ndarray, meta: _PanelMeta) -> pd.DataFrame:
    """Reconstruct the (date, ticker) OHLCV DataFrame from a raw float64 buffer."""

    index = pd.MultiIndex.from_arrays(
        [pd.Index(meta.date_values), pd.Index(meta.ticker_values)],
        names=list(RUNTIME_PANEL_INDEX_NAMES),
    )
    return pd.DataFrame(buffer, index=index, columns=list(meta.columns))


def _init_worker(meta: _PanelMeta) -> None:
    """Run once per worker: attach the shared-memory panel (read-only) to globals.

    The buffer is copied out of shared memory into a private array so the worker
    never writes back to the shared block (read-only contract, T-20-03).
    """

    global _WORKER_PANEL, _WORKER_SHM
    _WORKER_SHM = shared_memory.SharedMemory(name=meta.shm_name)
    raw = np.ndarray(meta.shape, dtype=np.float64, buffer=_WORKER_SHM.buf)
    _WORKER_PANEL = _rebuild_panel(np.array(raw, copy=True), meta)


def _run_into_queue(queue: mp.Queue, meta: _PanelMeta, payload: dict) -> None:
    """Worker entrypoint: rebuild panel + execute one candidate, push the result.

    Imports ``execute_alpha_function`` IN-WORKER so the restricted namespace is
    reconstructed in the child (security boundary preserved). Only basic picklable
    fields are returned; any failure is captured as an error string.
    """

    from cogalpha.execution import execute_alpha_function
    from cogalpha.schemas import AlphaFunction

    candidate_id = str(payload.get("candidate_id", ""))
    try:
        _init_worker(meta)
        alpha = AlphaFunction.model_validate(payload["alpha"])
        series = execute_alpha_function(alpha, _WORKER_PANEL)
        queue.put(
            {
                "candidate_id": candidate_id,
                "ok": True,
                "values": series.to_dict(),
                "name": series.name,
                "index": list(series.index),
            }
        )
    except Exception as exc:  # noqa: BLE001 - any failure is a fail-closed disposition
        queue.put(
            {
                "candidate_id": candidate_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


def _series_from_payload(payload: dict) -> pd.Series:
    """Rebuild the (date, ticker) factor Series from a worker payload."""

    index = pd.MultiIndex.from_tuples(
        payload["index"], names=list(RUNTIME_PANEL_INDEX_NAMES)
    )
    series = pd.Series(payload["values"], name=payload["name"])
    series.index = index
    return series.astype(float)


class AlphaExecPool:
    """Engine-level subprocess isolation service for untrusted alpha code.

    Holds the shared-memory panel + spawn context for the run; evaluates batches
    of candidates with per-task timeout->SIGKILL supervision and returns
    input-indexed :class:`AlphaExecResult` lists.
    """

    def __init__(self, panel: pd.DataFrame, *, concurrency: int | None = 1) -> None:
        sorted_panel = _validate_runtime_ohlcv_panel(panel)
        values = sorted_panel.loc[:, list(RUNTIME_OHLCV_COLUMNS)].to_numpy(dtype=np.float64)
        self._concurrency = resolve_max_workers(concurrency)
        self._context = mp.get_context("spawn")

        self._shm = shared_memory.SharedMemory(create=True, size=max(values.nbytes, 1))
        # CR-01: if any post-creation init step raises, the SharedMemory block is
        # orphaned (the object was never fully constructed, so the caller's
        # with/try cannot clean it up and `/dev/shm` leaks until reboot). Unlink on
        # failure so the exception path never leaks a kernel-backed block.
        try:
            buffer = np.ndarray(values.shape, dtype=np.float64, buffer=self._shm.buf)
            buffer[:] = values
            date_level, ticker_level = RUNTIME_PANEL_INDEX_NAMES
            self._meta = _PanelMeta(
                shm_name=self._shm.name,
                shape=values.shape,
                columns=tuple(RUNTIME_OHLCV_COLUMNS),
                date_values=tuple(sorted_panel.index.get_level_values(date_level)),
                ticker_values=tuple(sorted_panel.index.get_level_values(ticker_level)),
            )
        except BaseException:
            self._shm.close()
            self._shm.unlink()
            raise
        self._closed = False

    def evaluate_alpha_code(
        self,
        candidates: Sequence[AlphaCandidate],
        *,
        timeout_seconds: float,
    ) -> list[AlphaExecResult]:
        """Execute each candidate in isolation, returning input-indexed results.

        Candidates run in bounded batches of ``concurrency`` independent spawn
        processes. A candidate whose worker is still alive after
        ``timeout_seconds`` is SIGKILLed and dispositioned as an execution
        failure (``ok=False``, ``timed_out=True``). With ``concurrency<=1`` the
        candidates run strictly one at a time (deterministic serial path).
        """

        if self._closed:
            raise RuntimeError("AlphaExecPool has been closed.")

        items = list(candidates)
        results: list[AlphaExecResult | None] = [None] * len(items)
        batch_size = max(1, self._concurrency)
        for start in range(0, len(items), batch_size):
            batch = list(enumerate(items[start : start + batch_size], start=start))
            for index, candidate in batch:
                results[index] = self._run_one(candidate, timeout_seconds=timeout_seconds)
        return [result for result in results if result is not None]

    def _run_one(
        self,
        candidate: AlphaCandidate,
        *,
        timeout_seconds: float,
    ) -> AlphaExecResult:
        payload = {
            "candidate_id": candidate.candidate_id,
            "alpha": candidate.alpha.model_dump(mode="json"),
        }
        queue: mp.Queue = self._context.Queue()
        proc = self._context.Process(
            target=_run_into_queue,
            args=(queue, self._meta, payload),
        )
        proc.start()

        # CR-02: drain the result queue BEFORE joining. A worker that puts a large
        # `series.to_dict()` payload will NOT exit (its feeder thread blocks on a
        # full pipe) until the consumer reads it, so join-then-get is the classic
        # multiprocessing loss/deadlock pattern -- a slow-but-valid result could be
        # falsely dispositioned as a failure, breaking the parallel==serial
        # determinism contract (D-01). Bound the wait to the real per-task timeout,
        # not a flat 1.0s. WR-09: catch `Empty` specifically (the timeout case);
        # any other error surfaces through the malformed-payload disposition below.
        try:
            result_payload = queue.get(timeout=timeout_seconds)
        except Empty:
            result_payload = None

        if result_payload is None:
            # No result within the timeout. Distinguish a still-running CPU-bound
            # `while True` (alive -> SIGKILL, timed_out) from a worker that died
            # without producing output (segfault/crash -> execution failure).
            # future.result(timeout=) would NOT kill the loop -- SIGKILL is the
            # only thing that contains it (Pitfall 1).
            timed_out = proc.is_alive()
            if timed_out:
                proc.kill()
            proc.join()
            queue.close()
            if timed_out:
                return AlphaExecResult(
                    candidate_id=candidate.candidate_id,
                    ok=False,
                    error=f"Execution exceeded timeout_seconds={timeout_seconds}.",
                    timed_out=True,
                )
            return AlphaExecResult(
                candidate_id=candidate.candidate_id,
                ok=False,
                error="Worker process exited without returning a result.",
            )

        # Worker produced a result and is finishing; the join is prompt now that
        # the pipe has been drained.
        proc.join()
        queue.close()
        return self._disposition(candidate.candidate_id, result_payload)

    @staticmethod
    def _disposition(candidate_id: str, payload: dict) -> AlphaExecResult:
        if not payload.get("ok"):
            return AlphaExecResult(
                candidate_id=candidate_id,
                ok=False,
                error=str(payload.get("error", "Unknown execution failure.")),
            )
        try:
            factor_values = _series_from_payload(payload)
        except Exception as exc:  # noqa: BLE001 - malformed payload is fail-closed
            return AlphaExecResult(
                candidate_id=candidate_id,
                ok=False,
                error=f"Malformed worker payload: {type(exc).__name__}: {exc}",
            )
        return AlphaExecResult(
            candidate_id=candidate_id,
            ok=True,
            factor_values=factor_values,
        )

    def close(self) -> None:
        """Release the shared-memory panel (graceful shutdown)."""

        if self._closed:
            return
        self._closed = True
        try:
            self._shm.close()
        finally:
            self._shm.unlink()

    def __enter__(self) -> AlphaExecPool:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
