"""Bind the known diagnostic suite before any hardware observations."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from km_quantum.m4.io import canonical_hash, lf_file_hash
from km_quantum.m5.circuits import FrozenCircuit
from km_quantum.m5.io import dependency_versions, raw_hash
from km_quantum.m5.models import M4_COMMIT, M5Config


def source_context(root: Path) -> dict[str, Any]:
    paths = sorted(
        [*root.glob("src/**/*.py"), *root.glob("tests/**/*.py")]
        + [root / "pyproject.toml", root / "uv.lock"]
    )
    hashes = {path.relative_to(root).as_posix(): lf_file_hash(path) for path in paths}
    payload = {"source_files_lf_sha256": hashes, "dependencies": dependency_versions()}
    return {**payload, "content_sha256": canonical_hash(payload)}


def git_context(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(git("status", "--porcelain")),
    }


def selection_manifest(
    root: Path, suite: tuple[FrozenCircuit, ...], config: M5Config
) -> dict[str, Any]:
    protocol = root / "docs/m5_experiment_protocol.md"
    if not protocol.is_file():
        raise ValueError("Freeze the M5 experiment protocol before preparing circuits")
    return {
        "schema_version": 1,
        "artifact_type": "m5_precommitted_selection",
        "published_m4_commit": M4_COMMIT,
        "work_order_sha256": raw_hash(root / "KM3_M5_IBM_Hardware_Validation_Prompt.md"),
        "protocol_lf_sha256": lf_file_hash(protocol),
        "config": config.model_dump(mode="json"),
        "selection_rule": "All four predeclared known diagnostic cases; stored M4 restart 0 only",
        "holdout": False,
        "training_performed": False,
        "new_relaxations_performed": False,
        "circuits": [
            {
                "circuit_id": item.circuit_id,
                "reference_name": item.reference_name,
                "algorithm": item.algorithm,
                "p": item.p,
                "seed": item.seed,
                "source_hashes": item.source_hashes,
                "frozen_parameters": list(item.frozen_parameters),
                "parameter_order": list(item.parameter_order),
                "relaxation_y": list(item.relaxation_y),
                "epsilon": item.epsilon,
            }
            for item in suite
        ],
        "main_circuit_count": 24,
        "qaoa_circuit_count": 16,
        "control_circuit_count": 8,
        "hardware_enabled": False,
        "submission_approved": False,
    }
