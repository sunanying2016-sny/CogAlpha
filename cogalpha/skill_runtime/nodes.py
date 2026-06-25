"""Skill Node invocation helpers for paper-defined skill prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from cogalpha.schemas import AlphaCandidate, AlphaCandidateBatch, QualityDecision

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StructuredArtifactInvoker(Protocol):
    """Invoker shape required by Skill Nodes."""

    def invoke(
        self,
        skill_name: str,
        request: BaseModel,
        output_schema: type[SchemaT],
    ) -> SchemaT:
        """Invoke a skill and return an internal structured artifact."""


@dataclass(frozen=True)
class SkillNodeRuntime:
    """Invoke Standard Skills using Runtime Schema objects as the Interface."""

    invoker: StructuredArtifactInvoker

    def candidate_batch(self, skill_name: str, request: BaseModel) -> AlphaCandidateBatch:
        """Invoke a Domain Agent Skill."""

        return self._invoke_request(skill_name, request, AlphaCandidateBatch)

    def quality_decision(self, skill_name: str, request: BaseModel) -> QualityDecision:
        """Invoke a Quality Checker Skill."""

        return self._invoke_request(skill_name, request, QualityDecision)

    def alpha_candidate(self, skill_name: str, request: BaseModel) -> AlphaCandidate:
        """Invoke an Evolution Operator Skill."""

        return self._invoke_request(skill_name, request, AlphaCandidate)

    def _invoke_request(
        self,
        skill_name: str,
        request: BaseModel,
        output_schema: type[SchemaT],
    ) -> SchemaT:
        return self.invoker.invoke(skill_name, request, output_schema)
