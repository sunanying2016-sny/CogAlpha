"""Single structured JSON quality-verdict schema and parser (D-03 / AGENT-02+03).

Replaces the brittle per-skill free-text sniffing that previously lived in
``skill_runtime/io.py``. Every judge/quality decision now emits one JSON object
``{"status": "accept"|"reject"|"repair", "reasons": [...]}`` validated against a
single strict pydantic model and mapped to the existing
:class:`cogalpha.schemas.QualityVerdict` enum.

The parser is fail-closed: any output that cannot be validated against the schema
(including unknown ``status`` values and unexpected fields) maps to
``QualityVerdict.REJECT`` rather than silently accepting. Prose-wrapped JSON gets a
single recovery attempt (extract the first ``{...}`` object) that is NOT counted
against ``quality_repair_attempts``.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from cogalpha.schemas import QualityVerdict

__all__ = ["QualityVerdictModel", "parse_quality_verdict"]

# Matches the first balanced-looking ``{...}`` object in a prose-wrapped response.
# Non-greedy with DOTALL so the smallest enclosing object is preferred.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_STATUS_TO_VERDICT: dict[str, QualityVerdict] = {
    "accept": QualityVerdict.ACCEPT,
    "reject": QualityVerdict.REJECT,
    "repair": QualityVerdict.REPAIR,
}


class QualityVerdictModel(BaseModel):
    """Strict structured verdict emitted by every quality/judge skill (D-03)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["accept", "reject", "repair"]
    reasons: list[str]


def parse_quality_verdict(model_output: str) -> QualityVerdict:
    """Parse a model verdict string into a :class:`QualityVerdict`, fail-closed.

    Tries a direct ``json.loads`` first. On failure, a single recovery attempt
    extracts the first ``{...}`` object from prose and retries. Any persistent
    failure (invalid JSON, schema violation, unknown status) maps to ``REJECT``.
    """

    model = _try_parse_model(model_output)
    if model is None:
        recovered = _extract_first_json_object(model_output)
        if recovered is not None:
            model = _try_parse_model(recovered)
    if model is None:
        return QualityVerdict.REJECT
    return _STATUS_TO_VERDICT[model.status]


def parse_quality_verdict_model(model_output: str) -> QualityVerdictModel | None:
    """Return the validated verdict model, or ``None`` if it cannot be parsed.

    Exposes the structured ``reasons`` so callers (``io.py``) can build feedback
    text without re-parsing. Applies the same one-retry recovery as
    :func:`parse_quality_verdict`.
    """

    model = _try_parse_model(model_output)
    if model is None:
        recovered = _extract_first_json_object(model_output)
        if recovered is not None:
            model = _try_parse_model(recovered)
    return model


def _try_parse_model(payload: str) -> QualityVerdictModel | None:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return QualityVerdictModel.model_validate(data)
    except ValidationError:
        return None


def _extract_first_json_object(text: str) -> str | None:
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        return None
    return match.group(0)
