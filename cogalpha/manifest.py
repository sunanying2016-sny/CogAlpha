"""Run manifests for reproducible CogAlpha harness executions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class FileFingerprint(BaseModel):
    """Stable fingerprint for one file used by a run."""

    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str


class RunManifest(BaseModel):
    """Frozen run inputs for preflight or formal workflow execution."""

    model_config = ConfigDict(extra="forbid")

    manifest_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    purpose: str = Field(..., min_length=1)
    data_version: str
    config_files: list[FileFingerprint] = Field(default_factory=list)
    skill_files: list[FileFingerprint] = Field(default_factory=list)
    code_files: list[FileFingerprint] = Field(default_factory=list)
    fixed_inputs: list[str] = Field(default_factory=list)
    model_settings: dict[str, str] = Field(default_factory=dict)
    notes: str | None = None


def file_fingerprint(path: str | Path) -> FileFingerprint:
    """Return a SHA-256 fingerprint for one file."""

    file_path = Path(path)
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return FileFingerprint(path=str(file_path), sha256=digest)


def build_run_manifest(
    *,
    manifest_id: str,
    purpose: str,
    data_version: str,
    config_paths: list[str | Path],
    skill_paths: list[str | Path],
    code_paths: list[str | Path],
    fixed_inputs: list[str] | None = None,
    model_settings: dict[str, str] | None = None,
    notes: str | None = None,
) -> RunManifest:
    """Build a manifest from explicit run inputs."""

    return RunManifest(
        manifest_id=manifest_id,
        purpose=purpose,
        data_version=data_version,
        config_files=[file_fingerprint(path) for path in config_paths],
        skill_files=[file_fingerprint(path) for path in skill_paths],
        code_files=[file_fingerprint(path) for path in code_paths],
        fixed_inputs=fixed_inputs or [],
        model_settings=model_settings or {},
        notes=notes,
    )


def write_run_manifest(path: str | Path, manifest: RunManifest) -> None:
    """Write a manifest as stable JSON."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
