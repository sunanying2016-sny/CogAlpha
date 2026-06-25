"""Typed runtime state container for the paper-faithful CogAlpha engine.

This module owns the single typed ``CogAlphaState`` used by the deterministic
orchestrator (Wave 2+). State is backed by a central candidate store
(``dict[candidate_id, AlphaCandidate]``) that is the *only* place candidate
payloads live; each of the five separated pools holds ordered candidate-id
references, never duplicated payloads. This keeps the accumulated elite pool
bounded across the 24-generation loop (G1 bounded memory).

It also defines the STATE-03 stage/trace contracts as types only (the
``StageResult`` alias, ``InvocationRecord``, and reuse of the single
``CogAlphaTraceEvent``) and the D-08 ``semantic_factor_hash`` factor-code
normalizer used as the parent-pool de-duplication key. Wave 1 places these
types; their behavioral wiring into the stages/fitness flow happens in Wave 3.
"""

from __future__ import annotations

import ast
import hashlib

from pydantic import BaseModel, ConfigDict, Field

from cogalpha.schemas import AlphaCandidate

# STATE-03: reuse the single existing trace-event model. Do NOT define a new
# (fourth) event model here. Re-exported so downstream stage code imports the
# one canonical TraceEvent from cogalpha.state.
from cogalpha.tracing import CogAlphaTraceEvent

__all__ = [
    "CogAlphaState",
    "CogAlphaTraceEvent",
    "InvocationRecord",
    "StageResult",
    "semantic_factor_hash",
]


class CogAlphaState(BaseModel):
    """Single typed runtime state for the CogAlpha generation loop.

    Backed by a central candidate ``store``; the five pools hold ordered
    candidate-id references only (G1 bounded memory). Replaces the conflated
    three-pool state that misnamed ``qualified_pool`` and lacked
    ``candidate_pool`` / ``parent_pool``.
    """

    model_config = ConfigDict(extra="forbid")

    generation: int = Field(default=0, ge=0)
    store: dict[str, AlphaCandidate] = Field(
        default_factory=dict,
        description="Central candidate store: the only place payloads live.",
    )
    candidate_pool: list[str] = Field(
        default_factory=list,
        description="Newly generated candidate ids (pre-fitness).",
    )
    qualified_pool: list[str] = Field(
        default_factory=list,
        description="This generation's 65th-percentile qualified candidate ids.",
    )
    parent_pool: list[str] = Field(
        default_factory=list,
        description="Bounded dedup(elite_carry + qualified) parent candidate ids.",
    )
    elite_pool: list[str] = Field(
        default_factory=list,
        description=(
            "Accumulated 80th-percentile elite candidate ids "
            "(a.k.a. final_candidate_pool)."
        ),
    )
    rejected_pool: list[str] = Field(
        default_factory=list,
        description="Rejected candidate ids.",
    )


class InvocationRecord(BaseModel):
    """STATE-03 contract: one skill invocation and the candidates it produced.

    Carries the invocation-side skill->candidate linkage via ``candidate_ids``
    (attached at creation). The candidate-side linkage is carried by the
    existing ``EvolutionLineage.agent_skill`` field on ``AlphaCandidate``.
    Minimal field set (types-only); Wave 3 stages refine as needed.
    """

    model_config = ConfigDict(extra="forbid")

    skill_name: str
    candidate_ids: list[str] = Field(default_factory=list)


# STATE-03 contract: every stage returns (candidates, invocation_records).
StageResult = tuple[list[AlphaCandidate], list[InvocationRecord]]


# Names preserved (not canonicalized) so equivalent factors referencing the
# data frame and OHLCV columns fold together rather than being distinguished by
# positional renaming. ``df`` is the conventional input frame name.
_RESERVED: frozenset[str] = frozenset(
    {"df", "open", "high", "low", "close", "volume"}
)


class _NameCanonicalizer(ast.NodeTransformer):
    """Rename local variables/params to position-independent placeholders.

    Reserved names (the input frame + OHLCV column literals) are preserved so
    semantically equivalent factors collapse to the same canonical form.
    """

    def __init__(self) -> None:
        self._mapping: dict[str, str] = {}

    def _canon(self, name: str) -> str:
        if name in _RESERVED:
            return name
        return self._mapping.setdefault(name, f"v{len(self._mapping)}")

    def visit_Name(self, node: ast.Name) -> ast.AST:
        node.id = self._canon(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = self._canon(node.arg)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        # Canonicalize the factor's own name so equivalent factors that differ
        # only in their function name (different agents naming the same logic)
        # fold to the same digest rather than being spuriously distinguished
        # (D-08 / plan A5). generic_visit still descends into params/body.
        node.name = self._canon(node.name)
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        node.name = self._canon(node.name)
        self.generic_visit(node)
        return node


def _strip_docstrings(tree: ast.AST) -> None:
    """Remove module/function/class docstrings so comments never distinguish."""
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:]


def semantic_factor_hash(code: str) -> str:
    """Return a stable sha256 over the normalized AST of factor ``code``.

    Folds semantically equivalent factors (variable-name / whitespace / comment
    differences) to the same digest and distinguishes inequivalent ones. Used
    as the parent-pool de-duplication key (D-08); candidate identity (UUID)
    still keys the store.

    Security (T-17-04): the input is LLM-generated source. This function ONLY
    parses it with ``ast.parse`` and never executes it (no exec/eval/compile).
    """
    tree = ast.parse(code)
    _strip_docstrings(tree)
    tree = _NameCanonicalizer().visit(tree)
    ast.fix_missing_locations(tree)
    canonical = ast.unparse(tree)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
