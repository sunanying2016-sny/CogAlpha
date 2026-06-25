"""Engine-path secret-NAME policy (re-homed from ``cogalpha.closeout.secrets``).

This is the single source of truth for *which env-var NAMES* are treated as
credentials. It is a name-pattern policy, NOT a fixed allowlist: any env var whose
NAME matches :data:`SECRET_NAME_PATTERN` and whose value is at least
:data:`MIN_SECRET_VALUE_LEN` characters long is a credential -- so a secret exported
under any matching name (``ANTHROPIC_API_KEY``, ``LLM_TOKEN``, ``MODEL_SECRET``, ...)
is detected by every consumer (redaction, the evidence-side no-secret audit), not just
a hard-coded three-name list (WR-02).

Re-homed onto the engine import path so ``cogalpha.redaction`` (engine) no longer has to
reach into the quarantined evidence ``closeout`` package. This is a code-location move
only: the env-var NAME set returned is byte-identical to the pre-move behavior. The
evidence-side no-secret audit re-points its ``env_secret_names`` import here
(evidence -> engine, allowed).
"""

from __future__ import annotations

import os
import re

# WR-02: single secret-name policy shared by the audit, the redactor (cogalpha.redaction),
# and the strict guards. Any env var whose NAME matches this pattern and whose value is
# len >= MIN_SECRET_VALUE_LEN is treated as a credential -- not just a fixed 3-name
# allowlist -- so a credential exported under any other name is detected everywhere.
SECRET_NAME_PATTERN = re.compile(r"(API[_-]?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", re.I)
MIN_SECRET_VALUE_LEN = 8


def env_secret_names() -> tuple[str, ...]:
    """Return every ``os.environ`` name matching the shared secret-name policy (WR-02).

    Sorted for deterministic output. Only names whose value is non-empty and at least
    :data:`MIN_SECRET_VALUE_LEN` characters are included, mirroring the strict guard.
    """

    return tuple(
        sorted(
            name
            for name, value in os.environ.items()
            if SECRET_NAME_PATTERN.search(name) and value and len(value) >= MIN_SECRET_VALUE_LEN
        )
    )
