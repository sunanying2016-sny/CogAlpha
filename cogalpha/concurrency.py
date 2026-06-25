"""Bounded, order-preserving concurrency helper for independent node invokes.

This is pure ORCHESTRATION (Phase 16 D-04: scale data, not system). It parallelizes
the dispatch of INDEPENDENT per-step units (domain-agent invokes, evolution plans,
per-candidate quality checks) via a bounded ``ThreadPoolExecutor`` while guaranteeing
that results are collected back into the SAME order as a sequential loop.

Determinism contract:
- ``concurrency=1`` runs strictly sequentially in input order (identical to the prior
  ``for`` loop), so traces / lineage / checkpoints are byte-for-byte unchanged.
- ``concurrency>1`` parallelizes only the dispatch; results are returned indexed by
  their original position, so the RESULT ordering is independent of completion order.

Per-item exceptions are not swallowed here — callers that need per-item error
isolation (the nodes do) pass callables that capture and return their own errors,
exactly as the sequential bodies already do.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")


def resolve_max_workers(concurrency: int | None) -> int:
    """Normalize a concurrency knob to a valid worker count (min 1)."""

    if concurrency is None:
        return 1
    return max(1, int(concurrency))


def ordered_map(
    functions: Sequence[Callable[[], T]],
    *,
    concurrency: int | None = 1,
) -> list[T]:
    """Run each zero-arg callable, returning results in input order.

    With ``concurrency<=1`` the callables run sequentially in order (no threads),
    which exactly reproduces the prior synchronous behavior. With ``concurrency>1``
    dispatch is parallelized across a bounded pool, but the returned list is always
    ordered by the original index, never by completion time.
    """

    items = list(functions)
    workers = resolve_max_workers(concurrency)
    if workers <= 1 or len(items) <= 1:
        return [function() for function in items]

    results: list[T | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as executor:
        future_to_index = {
            executor.submit(function): index
            for index, function in enumerate(items)
        }
        for future in future_to_index:
            index = future_to_index[future]
            results[index] = future.result()
    return [result for result in results]  # type: ignore[misc]
