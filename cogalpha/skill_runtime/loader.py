"""Standard skill discovery and progressive-disclosure context assembly."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from cogalpha.skill_runtime.io import build_skill_prompt_template_values

FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\n(?P<content>.*)\Z", re.DOTALL)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+)\)")

# Name of the shared base-agent template directory (App C.1 contract). Domain
# overrides inherit it via a {base_contract} placeholder; the base itself is not
# a runnable skill and is excluded from discovery.
BASE_SKILL_DIR = "_base"
BASE_CONTRACT_PLACEHOLDER = "{base_contract}"
OVERRIDE_FIELD_RE = re.compile(
    r"<!--\s*(?P<key>[a-z_]+)\s*-->\n(?P<value>.*?)\n<!--\s*/(?P=key)\s*-->",
    re.DOTALL,
)

# App A.2 Diversified Guidance — the five rewording modes, injected as prompt
# directives (never code branches). Keyed by the modes in
# cogalpha.alpha_contract.DIVERSIFIED_GUIDANCE_MODES.
DIVERSIFIED_GUIDANCE_DIRECTIVES: dict[str, str] = {
    "light": (
        "Light rewording — keep the design intent nearly identical, improving only "
        "clarity and fluency for a consistent baseline."
    ),
    "moderate": (
        "Moderate rephrasing — restate the guidance naturally with mild enrichment "
        "and stylistic variation to test robustness under altered framing."
    ),
    "creative": (
        "Creative rewording — pursue expressive, research-oriented angles with "
        "interpretative depth that inspire novel analytical structures."
    ),
    "divergent": (
        "Divergent exploration — rewrite from new but relevant viewpoints, shifting "
        "emphasis to different sub-mechanisms within the same framework for broader "
        "factor diversity."
    ),
    "concrete": (
        "Concrete, implementation-oriented framing — introduce measurable quantities "
        "(statistical formulas, ratios, example computations) that bridge concept to "
        "implementation."
    ),
}
DEFAULT_GUIDANCE_DIRECTIVE = (
    "Apply balanced reasoning that preserves the agent's specialization while "
    "encouraging structurally diverse, economically interpretable factors."
)


@dataclass(frozen=True)
class SkillMetadata:
    """Frontmatter metadata discovered without loading every support file."""

    name: str
    description: str
    path: Path
    extra: Mapping[str, Any]
    allowed_tools: tuple[str, ...] = ()
    argument_hint: str | None = None
    argument_names: tuple[str, ...] = ()
    when_to_use: str | None = None
    version: str | None = None
    model: str | None = None
    effort: str | None = None
    context: str | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadedSkill:
    """One invoked skill and its direct support files."""

    metadata: SkillMetadata
    body: str
    references: Mapping[Path, str]


class SkillLoaderError(ValueError):
    """Raised when a skill cannot be discovered or assembled."""


class StandardSkillLoader:
    """Discover project-local skills and assemble model-facing prompts."""

    def __init__(self, skills_root: Path | str) -> None:
        self.skills_root = Path(skills_root)

    def discover(self) -> dict[str, SkillMetadata]:
        """Discover all skills under the configured root."""

        skills: dict[str, SkillMetadata] = {}
        for skill_file in sorted(self.skills_root.glob("*/SKILL.md")):
            if skill_file.parent.name == BASE_SKILL_DIR:
                continue
            metadata, _body = self._read_skill_file(skill_file)
            if metadata.name in skills:
                raise SkillLoaderError(f"Duplicate skill name: {metadata.name}")
            skills[metadata.name] = metadata
        return skills

    def load(self, skill_name: str) -> LoadedSkill:
        """Load one skill body and its directly linked support files."""

        discovered = self.discover()
        if skill_name not in discovered:
            raise SkillLoaderError(f"Unknown skill: {skill_name}")

        metadata = discovered[skill_name]
        _metadata, body = self._read_skill_file(metadata.path)
        references = self._load_direct_references(metadata.path, body)
        return LoadedSkill(metadata=metadata, body=body, references=references)

    def assemble_context(
        self,
        skill_name: str,
        request: BaseModel,
        inline_references: bool = False,
        arguments: str = "",
        session_id: str | None = None,
    ) -> str:
        """Compose one paper-defined prompt by filling template placeholders."""

        loaded = self.load(skill_name)
        body = self._resolve_base_contract(loaded.body)
        body = body.replace("$ARGUMENTS", arguments)
        body = self._inject_guidance_directive(body, request)
        for key, value in build_skill_prompt_template_values(request).items():
            body = body.replace("{" + key + "}", str(value))
        sections = [body.strip()]
        if inline_references and loaded.references:
            sections.append("# Reference Material")
            for path, content in loaded.references.items():
                rel_path = path.relative_to(self.skills_root.resolve())
                sections.append(f"## {rel_path}")
                sections.append(content.strip())
        return "\n\n".join(section for section in sections if section)

    def assemble_raw_body(self, skill_name: str) -> str:
        """Return one skill body with the base contract inherited, placeholders intact.

        Used by content-contract tests to validate the assembled (base + override)
        prompt surface without binding to a runtime request.
        """

        loaded = self.load(skill_name)
        body = self._resolve_base_contract(loaded.body)
        return self._inject_guidance_directive(body, request=None)

    def _resolve_base_contract(self, body: str) -> str:
        """Expand a domain override's {base_contract} placeholder with the shared base."""

        if BASE_CONTRACT_PLACEHOLDER not in body:
            return body
        base_path = self.skills_root / BASE_SKILL_DIR / "SKILL.md"
        if not base_path.exists():
            raise SkillLoaderError(f"Missing shared base template: {base_path}")
        _metadata, base_body = self._read_skill_file(base_path)
        fields = {
            match.group("key"): match.group("value").strip()
            for match in OVERRIDE_FIELD_RE.finditer(body)
        }
        resolved_base = base_body
        for key, value in fields.items():
            resolved_base = resolved_base.replace("{" + key + "}", value)
        override_shell = OVERRIDE_FIELD_RE.sub("", body)
        # Collapse the blank lines left behind where override fields were removed.
        override_shell = re.sub(r"\n{3,}", "\n\n", override_shell).strip()
        return override_shell.replace(BASE_CONTRACT_PLACEHOLDER, resolved_base)

    def _inject_guidance_directive(self, body: str, request: BaseModel | None) -> str:
        """Fill the {guidance_directive} placeholder from the request's guidance mode.

        The directive is injected as prompt text (App A.2), never a code branch.
        """

        if "{guidance_directive}" not in body and "{guidance_mode}" not in body:
            return body
        mode = getattr(request, "guidance_mode", None) if request is not None else None
        directive = DIVERSIFIED_GUIDANCE_DIRECTIVES.get(
            mode or "", DEFAULT_GUIDANCE_DIRECTIVE
        )
        body = body.replace("{guidance_directive}", directive)
        return body.replace("{guidance_mode}", mode or "balanced")

    def _read_skill_file(self, path: Path) -> tuple[SkillMetadata, str]:
        text = path.read_text(encoding="utf-8")
        match = FRONTMATTER_RE.match(text)
        if not match:
            raise SkillLoaderError(f"Missing frontmatter: {path}")

        frontmatter = _parse_simple_frontmatter(match.group("body"))
        name = str(frontmatter.pop("name", "")).strip()
        description = str(frontmatter.pop("description", "")).strip()
        if not name or not description:
            raise SkillLoaderError(f"Skill frontmatter requires name and description: {path}")

        metadata = SkillMetadata(
            name=name,
            description=description,
            path=path,
            extra=frontmatter,
            allowed_tools=_parse_list(frontmatter.pop("allowed-tools", "")),
            argument_hint=_none_if_empty(str(frontmatter.pop("argument-hint", ""))),
            argument_names=_parse_list(frontmatter.pop("arguments", "")),
            when_to_use=_none_if_empty(str(frontmatter.pop("when_to_use", ""))),
            version=_none_if_empty(str(frontmatter.pop("version", ""))),
            model=_none_if_empty(str(frontmatter.pop("model", ""))),
            effort=_none_if_empty(str(frontmatter.pop("effort", ""))),
            context=_none_if_empty(str(frontmatter.pop("context", ""))),
            disable_model_invocation=_parse_bool(
                frontmatter.pop("disable-model-invocation", "")
            ),
            user_invocable=_parse_bool(frontmatter.pop("user-invocable", ""), default=True),
            paths=_parse_list(frontmatter.pop("paths", "")),
        )
        return metadata, match.group("content")

    def _load_direct_references(self, skill_path: Path, body: str) -> dict[Path, str]:
        references: dict[Path, str] = {}
        skill_dir = skill_path.parent

        for target in MARKDOWN_LINK_RE.findall(body):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if not target.endswith(".md"):
                continue

            target_path = (skill_dir / target).resolve()
            try:
                target_path.relative_to(self.skills_root.resolve())
            except ValueError:
                raise SkillLoaderError(f"Reference escapes skills root: {target}") from None

            if target_path == skill_path.resolve():
                continue
            if not target_path.exists():
                raise SkillLoaderError(f"Missing direct reference: {target}")
            references[target_path] = target_path.read_text(encoding="utf-8")

        return references


def _parse_simple_frontmatter(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if current_list_key and stripped.startswith("- "):
            current = values.setdefault(current_list_key, [])
            if isinstance(current, list):
                current.append(stripped[2:].strip().strip('"').strip("'"))
            continue
        current_list_key = None
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            values[key] = []
            current_list_key = key
            continue
        values[key] = _parse_scalar_or_inline_list(value)
    return values


def _parse_scalar_or_inline_list(value: str) -> Any:
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return list(_parse_list(stripped))
    return stripped.strip('"').strip("'")


def _none_if_empty(value: str) -> str | None:
    return value if value else None


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if not value:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_list(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(part).strip().strip('"').strip("'") for part in value if str(part).strip())
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1]
    return tuple(part.strip().strip('"').strip("'") for part in stripped.split(",") if part.strip())
