"""Portable public artifact identity and separately diagnosed historical raw bytes."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from km_quantum.m4.io import canonical_hash, lf_file_hash, read_json
from km_quantum.m5.models import M4_COMMIT

INTEGRATION_PATHS = frozenset(
    ("README.md", "pyproject.toml", "uv.lock", "src/km_quantum/__main__.py")
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def raw_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, payload: Any, *, immutable: bool = False) -> None:
    """Persist one complete JSON file; never overwrite a different immutable artifact."""
    if immutable and path.exists():
        if read_json(path) != payload:
            raise ValueError(f"Immutable M5 artifact differs: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            temporary = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def sealed_json(path: Path, payload: dict[str, Any], *, immutable: bool = True) -> dict[str, Any]:
    if "content_sha256" in payload:
        raise ValueError("Caller must not supply a preexisting artifact seal")
    artifact = {**payload, "content_sha256": canonical_hash(payload)}
    atomic_json(path, artifact, immutable=immutable)
    return artifact


def read_sealed(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    if not isinstance(artifact, dict) or "content_sha256" not in artifact:
        raise ValueError(f"M5 artifact has no content seal: {path.name}")
    payload = {key: value for key, value in artifact.items() if key != "content_sha256"}
    if artifact["content_sha256"] != canonical_hash(payload):
        raise ValueError(f"M5 content seal mismatch: {path.name}")
    return artifact


def historical_integrity(root: Path, *, include_after_hashes: bool = False) -> dict[str, Any]:
    provenance = read_json(root / "data/synthetic/m5/provenance.json")
    if provenance["published_m4_commit"] != M4_COMMIT:
        raise ValueError("The M5 baseline is not the published M4 reference")
    changed, line_endings, integrations = [], [], []
    after = {}
    for relative, expected in sorted(provenance["tracked_files_sha256_before"].items()):
        path = root / relative
        actual = raw_hash(path) if path.is_file() else None
        after[relative] = actual
        if actual == expected:
            continue
        diagnostic = {"path": relative, "before_sha256": expected, "after_sha256": actual}
        if relative in INTEGRATION_PATHS:
            integrations.append(diagnostic)
        elif (
            path.is_file()
            and relative in provenance["tracked_files_lf_sha256_before"]
            and lf_file_hash(path) == provenance["tracked_files_lf_sha256_before"][relative]
        ):
            line_endings.append(diagnostic)
        else:
            changed.append(diagnostic)
    result: dict[str, Any] = {
        "status": "PASS" if not changed else "FAIL",
        "published_m4_commit": M4_COMMIT,
        "baseline_file_count": provenance["tracked_file_count"],
        "protected_file_count": provenance["tracked_file_count"] - len(INTEGRATION_PATHS),
        "protected_content_unchanged": not changed,
        "protected_raw_bytes_unchanged": not changed and not line_endings,
        "changed": changed,
        "line_ending_only_changes": line_endings,
        "allowed_integration_changes": integrations,
        "binary_normalization_permitted": False,
    }
    if include_after_hashes:
        result["tracked_files_sha256_after"] = after
    return result


def dependency_versions() -> dict[str, str]:
    return {
        name: version(name)
        for name in (
            "qiskit",
            "qiskit-aer",
            "qiskit-ibm-runtime",
            "numpy",
            "scipy",
            "pydantic",
            "matplotlib",
            "ortools",
            "threadpoolctl",
        )
    }


def output_directory(root: Path, requested: str | Path) -> Path:
    result = Path(requested)
    result = (root / result).resolve() if not result.is_absolute() else result.resolve()
    base = (root / "results").resolve()
    try:
        relative = result.relative_to(base)
    except ValueError as error:
        raise ValueError("M5 artifacts must stay in the repository results directory") from error
    if not relative.parts or not relative.parts[0].startswith("m5"):
        raise ValueError("Use a dedicated results/m5... output; historical results are immutable")
    return result
