"""Stage call-site interfaces for the deterministic §2 orchestrator (Wave 2).

These are the typed dependency-injection contracts the orchestrator consumes;
Wave 3 fills them with the paper-faithful stage implementations. They are
defined as :class:`typing.Protocol`s (structural typing, no ABC inheritance
required of callers), mirroring the ``llm/client.py:CompletionClient`` idiom.

Per D-07 the interface shape is *dual* and must not be collapsed:

- Generation-type stages (generation / quality / evolution / injection) consume
  the live :class:`~cogalpha.state.CogAlphaState` and return a
  :data:`~cogalpha.state.StageResult`
  (``tuple[list[AlphaCandidate], list[InvocationRecord]]``). The generation
  stage additionally receives an adaptive ``feedback`` object (§3.5).
- ``fitness`` is a *distinct* partition interface: it reads state and writes the
  elite / qualified->parent / rejected pools per §3.4 and returns ``None`` — it
  is NEVER forced into a ``StageResult`` with empty candidates plus side effects.

:class:`CheckpointWriter` is the §2 ``checkpoint(state, g)`` call-site contract
(D-09); Phase 18 ships an in-memory / no-op default, the durable per-generation
file writer + ``--resume`` I/O is CONC-06 / Phase 20.

The orchestrator consumes the new ``state.py:CogAlphaState`` and
``state.StageResult`` — NOT the deprecated ``schemas.CogAlphaState``.
"""

from __future__ import annotations

from typing import Protocol

from cogalpha.state import CogAlphaState, StageResult

__all__ = [
    "CheckpointWriter",
    "EvolutionStage",
    "FitnessStage",
    "GenerationStage",
    "InjectionStage",
    "QualityStage",
]


class GenerationStage(Protocol):
    """§3.1+3.2+3.5 generation: adaptive-seeded candidate generation.

    Consumes the live state plus the adaptive ``feedback`` (the
    ``{effective_CoT}`` / ``{ineffective_CoT}`` guiding samples) and returns the
    newly generated candidates + their invocation records.
    """

    def __call__(self, state: CogAlphaState, feedback: object) -> StageResult: ...


class QualityStage(Protocol):
    """§3.3 multi-agent quality checker: filters/repairs incoming candidates.

    Consumes the candidates a prior stage produced (carried on the state) and
    returns the quality-accepted candidates + their invocation records.
    """

    def __call__(self, state: CogAlphaState, feedback: object) -> StageResult: ...


class EvolutionStage(Protocol):
    """§3.6 thinking evolution: mutate/cross the parent pool into children."""

    def __call__(self, state: CogAlphaState, feedback: object) -> StageResult: ...


class InjectionStage(Protocol):
    """§4.1 injection: task-agent alphas injected every ``injection_every`` gens."""

    def __call__(self, state: CogAlphaState, feedback: object) -> StageResult: ...


class FitnessStage(Protocol):
    """§3.4 fitness partition — DISTINCT shape (D-07).

    Reads the candidate pool, scores it, and writes the elite /
    qualified->parent / rejected pools in place. Produces no new candidates, so
    it returns ``None`` rather than a ``StageResult``.
    """

    def __call__(self, state: CogAlphaState) -> None: ...


class CheckpointWriter(Protocol):
    """§2 ``checkpoint(state, g)`` call-site (D-09).

    Phase 18 ships an in-memory / no-op default; the durable
    ``checkpoints/gen-<g>.json`` writer + ``--resume`` file I/O is CONC-06 /
    Phase 20.
    """

    def write(self, state: CogAlphaState, generation: int) -> None: ...
