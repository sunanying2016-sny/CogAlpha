"""Skill prompt variable rendering and model-output parsing."""

from __future__ import annotations

import ast
import hashlib
import re
from typing import Any

from pydantic import BaseModel

from cogalpha.alpha_contract import DEFAULT_ALPHA_LIBRARY_ALIASES, DEFAULT_OHLCV_COLUMNS
from cogalpha.schemas import (
    AlphaCandidate,
    AlphaCandidateBatch,
    AlphaFunction,
    DomainAgentRequest,
    EvolutionLineage,
    EvolutionOperation,
    EvolutionSkillRequest,
    QualityDecision,
    QualitySkillRequest,
    QualityVerdict,
    SkillKind,
    SkillRef,
)
from cogalpha.skill_runtime.verdict import (
    parse_quality_verdict,
    parse_quality_verdict_model,
)

FACTOR_FUNCTION_BLOCK_PATTERN = re.compile(
    r"<<function(?:\s+\d+)?>>\s*(?P<code>.*?)\s*<</function(?:\s+\d+)?>>",
    re.DOTALL | re.IGNORECASE,
)


class SkillOutputParseError(ValueError):
    """Raised when a skill model output does not match the expected artifact format."""


def parse_skill_model_output(
    *,
    skill_name: str,
    model_output: str,
    request: BaseModel,
    artifact_schema: type[BaseModel],
) -> BaseModel:
    """Parse one skill model output into the internal artifact schema."""

    if artifact_schema is AlphaCandidateBatch:
        return AlphaCandidateBatch(
            candidates=[
                _build_candidate_from_function_source(skill_name, code, request, index)
                for index, code in enumerate(
                    _extract_factor_function_blocks(model_output),
                    start=1,
                )
            ]
        )
    if artifact_schema is AlphaCandidate:
        code = _extract_factor_function_blocks(model_output)[0]
        return _build_candidate_from_function_source(skill_name, code, request, 1)
    if artifact_schema is QualityDecision:
        if not isinstance(request, QualitySkillRequest):
            raise SkillOutputParseError("QualityDecision requires a QualitySkillRequest.")
        return _parse_quality_decision(skill_name, model_output, request)
    raise SkillOutputParseError(
        f"Unsupported skill artifact schema: {artifact_schema.__name__}"
    )


_QUALITY_SKILLS: frozenset[str] = frozenset(
    {
        "alpha-code-quality",
        "alpha-code-repair",
        "alpha-judge",
        "alpha-logic-improvement",
    }
)
# Skills that emit a repaired <<function N>> block alongside their verdict.
_REPAIR_SKILLS: frozenset[str] = frozenset(
    {"alpha-code-repair", "alpha-logic-improvement", "alpha-code-quality"}
)


def _parse_quality_decision(
    skill_name: str,
    model_output: str,
    request: QualitySkillRequest,
) -> QualityDecision:
    """Parse any quality skill output through ONE structured JSON verdict schema.

    All four quality skills emit ``{"status", "reasons"}``; the single parser maps
    status to the QualityVerdict enum (fail-closed REJECT on unparseable output,
    D-03). Repair/improve skills additionally carry a repaired ``<<function N>>``
    block, which is still extracted via the preserved envelope parser.
    """

    if skill_name not in _QUALITY_SKILLS:
        raise SkillOutputParseError(f"Unsupported quality skill: {skill_name}")

    model = parse_quality_verdict_model(model_output)
    verdict = parse_quality_verdict(model_output)
    reasons = model.reasons if model is not None else []
    feedback = "\n".join(reasons).strip() or model_output.strip() or "No feedback provided."
    practical_soundness = (
        "; ".join(reasons).strip()
        if reasons
        else _PRACTICAL_SOUNDNESS_DEFAULTS.get(verdict, "Quality verdict produced.")
    )

    repaired = None
    if skill_name in _REPAIR_SKILLS and verdict != QualityVerdict.REJECT:
        repaired = _parse_first_repaired_candidate(skill_name, model_output, request)

    return QualityDecision(
        skill=_build_skill_ref(skill_name),
        verdict=verdict,
        practical_soundness=practical_soundness,
        feedback=feedback,
        repaired_candidate=repaired,
    )


_PRACTICAL_SOUNDNESS_DEFAULTS: dict[QualityVerdict, str] = {
    QualityVerdict.ACCEPT: "The code is correct.",
    QualityVerdict.REPAIR: "The code needs some adjustments.",
    QualityVerdict.REJECT: "The code was rejected.",
}


def _parse_first_repaired_candidate(
    skill_name: str,
    model_output: str,
    request: QualitySkillRequest,
) -> AlphaCandidate | None:
    blocks = _extract_factor_function_blocks(model_output, required=False)
    if not blocks:
        return None
    return _build_candidate_from_function_source(skill_name, blocks[0], request, 1)


def _extract_factor_function_blocks(
    model_output: str,
    *,
    required: bool = True,
) -> list[str]:
    blocks = [
        match.group("code").strip()
        for match in FACTOR_FUNCTION_BLOCK_PATTERN.finditer(model_output)
    ]
    if required and not blocks:
        raise SkillOutputParseError("Expected at least one <<function N>> block.")
    return blocks


def _build_candidate_from_function_source(
    skill_name: str,
    code: str,
    request: BaseModel,
    index: int,
) -> AlphaCandidate:
    name, docstring = _extract_factor_function_metadata(code)
    generation = _request_generation(request)
    lineage = EvolutionLineage(
        operation=_request_operation(request),
        parent_ids=_request_parent_ids(request),
        generation=generation,
        agent_skill=skill_name,
        guidance_mode=getattr(request, "guidance_mode", None),
    )
    digest = hashlib.sha1(code.encode("utf-8")).hexdigest()[:10]
    return AlphaCandidate(
        candidate_id=f"{skill_name}-g{generation}-f{index}-{digest}",
        alpha=AlphaFunction(
            name=name,
            code=code,
            formula=_derive_formula_from_docstring(docstring, name),
            rationale=docstring or f"{name} computes an OHLCV alpha factor.",
            required_columns=_infer_required_ohlcv_columns(code),
            allowed_libraries=list(DEFAULT_ALPHA_LIBRARY_ALIASES),
        ),
        lineage=lineage,
    )


def _extract_factor_function_metadata(code: str) -> tuple[str, str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise SkillOutputParseError(f"Generated function has invalid syntax: {exc}") from exc
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1:
        raise SkillOutputParseError("Expected exactly one top-level factor function.")
    function = functions[0]
    if not function.name.startswith("factor_"):
        raise SkillOutputParseError("Factor function name must start with factor_.")
    return function.name, ast.get_docstring(function) or ""


def _derive_formula_from_docstring(docstring: str, name: str) -> str:
    if not docstring:
        return f"{name}(OHLCV)"
    for line in docstring.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("formula:"):
            return stripped.split(":", 1)[1].strip() or stripped
        return stripped
    return f"{name}(OHLCV)"


def _infer_required_ohlcv_columns(code: str) -> list[str]:
    used = [
        column
        for column in DEFAULT_OHLCV_COLUMNS
        if repr(column) in code or f'"{column}"' in code
    ]
    return used or list(DEFAULT_OHLCV_COLUMNS)


def _request_generation(request: BaseModel) -> int:
    return int(getattr(request, "generation", 0))


def _request_operation(request: BaseModel) -> EvolutionOperation | None:
    if isinstance(request, EvolutionSkillRequest):
        return getattr(request, "operation", None)
    return None


def _request_parent_ids(request: BaseModel) -> list[str]:
    if isinstance(request, EvolutionSkillRequest):
        return [parent.candidate_id for parent in request.parents]
    if isinstance(request, QualitySkillRequest):
        return list(request.candidate.lineage.parent_ids)
    return []


def _build_skill_ref(skill_name: str) -> SkillRef:
    if skill_name in {
        "alpha-code-quality",
        "alpha-code-repair",
        "alpha-judge",
        "alpha-logic-improvement",
    }:
        kind = SkillKind.QUALITY_CHECKER
    else:
        kind = SkillKind.EVOLUTION_OPERATOR
    return SkillRef(name=skill_name, path=f"skills/{skill_name}/SKILL.md", kind=kind)


def build_skill_prompt_template_values(request: BaseModel) -> dict[str, Any]:
    """Build replacement values for a skill prompt template."""

    payload = request.model_dump(mode="python", exclude_none=True)
    values: dict[str, Any] = {key: _stringify(value) for key, value in payload.items()}

    if isinstance(request, DomainAgentRequest):
        values.update(
            {
                "num_per_request": str(request.num_candidates),
                "factor_type": request.factor_type or request.focus,
                "effective_CoT": request.effective_feedback_summary
                or "No successful cases are available yet.",
                "ineffective_CoT": request.ineffective_feedback_summary
                or "No failed cases are available yet.",
            }
        )

    if isinstance(request, QualitySkillRequest):
        values.update(
            {
                "columns_num": str(len(DEFAULT_OHLCV_COLUMNS)),
                "columns_desc": "\n".join(DEFAULT_OHLCV_COLUMNS),
                "code": request.candidate.alpha.code,
                "old_code": request.candidate.alpha.code,
                "dynamic_feedback": _build_quality_feedback_text(request),
                "error": _build_quality_feedback_text(request),
            }
        )

    if isinstance(request, EvolutionSkillRequest):
        parents = request.parents
        values.update(
            {
                "intro": _build_evolution_intro_text(request),
                "extra_guidance": _build_evolution_guidance_text(request),
                "original_factor_code": parents[0].alpha.code if parents else "",
                "parent_factor_1_code": parents[0].alpha.code if len(parents) >= 1 else "",
                "parent_factor_2_code": parents[1].alpha.code if len(parents) >= 2 else "",
            }
        )

    return values


def _build_quality_feedback_text(request: QualitySkillRequest) -> str:
    parts: list[str] = []
    for report in request.guard_reports:
        for issue in report.issues:
            parts.append(f"{report.guard_name}: {issue.code}: {issue.message}")
    for decision in request.previous_decisions:
        parts.append(f"{decision.skill.name}: {decision.verdict}: {decision.feedback}")
    if request.feedback:
        parts.append(request.feedback)
    return "\n".join(parts) or "No additional feedback was provided."


def _build_evolution_intro_text(request: EvolutionSkillRequest) -> str:
    parts = [f"Generation: {request.generation}. Operation: {request.operation.value}."]
    if request.effective_feedback_summary:
        parts.append(f"Effective factor summary:\n{request.effective_feedback_summary}")
    if request.ineffective_feedback_summary:
        parts.append(f"Ineffective factor summary:\n{request.ineffective_feedback_summary}")
    return "\n\n".join(parts)


def _build_evolution_guidance_text(request: EvolutionSkillRequest) -> str:
    guidance = []
    if request.effective_feedback_summary:
        guidance.append("Preserve useful principles from effective factors.")
    if request.ineffective_feedback_summary:
        guidance.append("Avoid failure modes observed in ineffective factors.")
    return "\n".join(f"- {item}" for item in guidance) or "- Preserve clear economic intuition."


def _stringify(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(_stringify(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_stringify(child)}" for key, child in value.items())
    return str(value)
