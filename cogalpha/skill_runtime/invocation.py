"""Skill invocation orchestration shared by Skill Nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from cogalpha.llm import CompletionClient
from cogalpha.skill_runtime.io import parse_skill_model_output
from cogalpha.skill_runtime.loader import SkillLoaderError, StandardSkillLoader

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass(frozen=True)
class SkillInvocationContext:
    """Assembled skill invocation plus frontmatter-derived policy metadata."""

    skill_name: str
    prompt: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SkillInvoker:
    """Assemble standard skill context, call an LLM, and validate the artifact."""

    loader: StandardSkillLoader
    client: CompletionClient
    inline_references: bool = False

    def invoke(
        self,
        skill_name: str,
        request: BaseModel,
        output_schema: type[SchemaT],
    ) -> SchemaT:
        context = self.prepare_context(skill_name, request, output_schema)
        model_output = self.client.complete_text(context.prompt)
        return output_schema.model_validate(
            parse_skill_model_output(
                skill_name=skill_name,
                model_output=model_output,
                request=request,
                artifact_schema=output_schema,
            )
        )

    def prepare_context(
        self,
        skill_name: str,
        request: BaseModel,
        output_schema: type[SchemaT],
    ) -> SkillInvocationContext:
        metadata = self.loader.discover().get(skill_name)
        if metadata is None:
            raise SkillLoaderError(f"Unknown skill: {skill_name}")
        if metadata.disable_model_invocation:
            raise PermissionError(
                f"Skill {skill_name!r} has disable-model-invocation enabled"
            )

        prompt = self.loader.assemble_context(
            skill_name=skill_name,
            request=request,
            inline_references=self.inline_references,
        )
        return SkillInvocationContext(
            skill_name=skill_name,
            prompt=prompt,
            metadata={
                "allowed_tools": list(metadata.allowed_tools),
                "model": metadata.model,
                "effort": metadata.effort,
                "context": metadata.context,
                "argument_hint": metadata.argument_hint,
                "argument_names": list(metadata.argument_names),
                "paths": list(metadata.paths),
            },
        )
