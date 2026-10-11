"""Deterministic additive artifacts and immutable historical reference checks."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

from km_quantum.qaoa.simulation import environment_metadata


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: Any, *, immutable: bool = False) -> None:
    path = Path(path)
    if immutable and path.exists():
        if read_json(path) != value:
            raise ValueError(f"Frozen artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def lf_file_hash(path: str | Path) -> str:
    """Portable artifact identity; only Git CRLF/LF transformations are normalized."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def verify_reference_integrity(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    provenance = read_json(root / "data/synthetic/m4/provenance.json")
    changed, line_endings = [], []
    for relative, expected in sorted(provenance["protected_files_sha256"].items()):
        path = root / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual == expected:
            continue
        diagnostic = {"path": relative, "expected_sha256": expected, "actual_sha256": actual}
        if (
            path.is_file()
            and lf_file_hash(path) == provenance["protected_files_lf_sha256"][relative]
        ):
            line_endings.append(diagnostic)
        else:
            changed.append(diagnostic)
    return {
        "unchanged": not changed,
        "protected_file_count": provenance["protected_file_count"],
        "protected_bytes_unchanged": not changed and not line_endings,
        "changed": changed,
        "line_ending_only_changes": line_endings,
        "m3_reference_commit": provenance["m3_reference_commit"],
    }


def execution_environment(root: str | Path) -> dict[str, Any]:
    result = environment_metadata(Path(root))
    result["dependency_lock_lf_sha256"] = lf_file_hash(Path(root) / "uv.lock")
    result["dependencies"] = {
        name: version(name)
        for name in (
            "qiskit",
            "qiskit-aer",
            "scipy",
            "numpy",
            "matplotlib",
            "ortools",
            "pydantic",
            "threadpoolctl",
            "psutil",
        )
    }
    return result
