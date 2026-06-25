"""Neutral shared redaction + JSONL helpers for the engine artifact path.

This module is import-clean of ``evidence.validation`` / ``cogalpha.artifacts`` so both
the engine-level artifact writer (``cogalpha.artifacts``) and the quarantined validation
evidence helpers (``evidence.validation.evidence``) can share one implementation without
a ``validation -> artifacts`` reverse dependency (Phase-21 quarantine, Open Q1 = b).

The redaction logic is byte-faithful to the previous ``validation/evidence.py`` definitions:
env-only secret values are redacted using the shared secret-name policy
(``cogalpha.secrets.env_secret_names``), never a hand-rolled allowlist.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from cogalpha.secrets import env_secret_names


def _redact_secret_values(text: str | None) -> str:
    # WR-02: derive the redacted name set from the shared secret-name policy
    # (cogalpha.secrets.env_secret_names) instead of a fixed 3-name allowlist, so a
    # credential exported under any matching env name is redacted, not only the legacy three.
    if not text:
        return ""
    redacted = text
    for name in env_secret_names():
        value = os.environ.get(name)
        if value:
            redacted = redacted.replace(value, f"<redacted:{name}>")
    return redacted


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "skill"


def _append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")
