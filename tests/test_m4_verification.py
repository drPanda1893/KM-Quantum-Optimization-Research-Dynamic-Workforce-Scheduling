"""Technical gate reports reject changed identities and failed physical checks."""

import copy
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from km_quantum.m4 import verification
from km_quantum.m4.io import canonical_hash, lf_file_hash, read_json
from km_quantum.m4.models import M4Config
from km_quantum.m4.noise import DEFAULT_NOISE_PROFILES, CompilerConfig
from km_quantum.m4.verification import verify_m4
from km_quantum.qaoa.references import FrozenReference, load_references

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def small_suite(monkeypatch: pytest.MonkeyPatch) -> dict[str, FrozenReference]:
    references = {"A_single_job": load_references(ROOT)["A_single_job"]}
    monkeypatch.setattr(verification, "load_m4_references", lambda root: references)
    monkeypatch.setattr(
        verification,
        "verify_reference_integrity",
        lambda root: {
            "unchanged": True,
            "protected_file_count": 2107,
            "protected_bytes_unchanged": True,
            "changed": [],
            "m3_reference_commit": "a" * 40,
        },
    )
    monkeypatch.setattr(
        verification,
        "_historical_audits",
        lambda root, integrity: {
            "m3_saved_rows_rechecked": 660,
            "m3_reference_commit": integrity["m3_reference_commit"],
        },
    )
    return references


def _manifest(config: M4Config, references: dict[str, FrozenReference]) -> dict[str, Any]:
    manifest = {
        "config_hash": canonical_hash(config.model_dump(mode="json")),
        "configuration": config.model_dump(mode="json"),
        "m3_reference_commit": "a" * 40,
        "noise_profiles": [asdict(profile) for profile in DEFAULT_NOISE_PROFILES],
        "compiler": asdict(CompilerConfig(seed=config.transpiler_seed)),
        "references": [
            {
                "reference_id": name,
                "reference_hash": reference.reference_hash,
                "qubo_hash": reference.qubo_hash,
                "ising_hash": reference.ising_hash,
                "qubit_count": reference.qubo.number_variables,
            }
            for name, reference in references.items()
        ],
    }
    manifest["manifest_content_sha256"] = canonical_hash(manifest)
    return manifest


def _seal(manifest: dict[str, Any]) -> None:
    manifest["manifest_content_sha256"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_content_sha256"}
    )


def test_executed_small_gate_persists_actual_independent_correctness_evidence(
    small_suite: dict[str, FrozenReference],
    tmp_path: Path,
) -> None:
    config = M4Config(depths=(1,), restart_seeds=(0,))
    report = verify_m4(ROOT, tmp_path, config, _manifest(config, small_suite))
    assert report == read_json(tmp_path / "verification.json")
    assert report["status"] == "PASS"
    assert (
        report["manifest_content_sha256"]
        == _manifest(config, small_suite)["manifest_content_sha256"]
    )
    assert report["checked_states"] == 4
    assert report["zero_noise_circuit_count"] == 2
    assert len(report["warm_standard_parity_rows"]) == 2
    assert all(
        report[name]
        for name in (
            "metric_semantics_verified",
            "operator_equivalence",
            "warm_start_mixer_verified",
            "zero_noise_equivalence_verified",
            "readout_model_verified",
        )
    )
    assert report["max_warm_standard_amplitude_difference"] < 1e-12
    assert report["mixer_matrix_gate"]["maximum_matrix_difference"] < 1e-12
    assert report["readout_channel_gate"]["two_qubit_readout_true_basis_index"] == 1
    assert report["resources"]["training_runs"] == 2
    assert report["resources"]["density_runs"] == 10
    assert report["quality_training_performed"] is False and report["hardware_used"] is False


@pytest.mark.parametrize(
    "changed",
    [
        "config_hash",
        "configuration",
        "m3_reference_commit",
        "reference_hash",
        "qubo_hash",
        "ising_hash",
        "qubit_count",
        "noise_profiles",
        "compiler",
        "seal",
    ],
)
def test_manifest_identity_changes_fail_before_circuit_execution(
    small_suite: dict[str, FrozenReference],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    config = M4Config(depths=(1,), restart_seeds=(0,))
    manifest = _manifest(config, small_suite)
    if changed in {"reference_hash", "qubo_hash", "ising_hash"}:
        manifest["references"][0][changed] = "wrong"
    elif changed == "qubit_count":
        manifest["references"][0][changed] += 1
    elif changed == "configuration":
        manifest[changed]["epsilon"] = 0.5
    elif changed == "noise_profiles":
        manifest[changed][1]["lambda_1q"] = 0.2
    elif changed == "compiler":
        manifest[changed]["seed"] += 1
    elif changed == "seal":
        manifest["manifest_content_sha256"] = "wrong"
    else:
        manifest[changed] = "wrong"
    if changed != "seal":
        _seal(manifest)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Identity failure must occur before constructing a quantum circuit")

    monkeypatch.setattr(verification, "compile_bound_circuit", forbidden)
    report = verify_m4(ROOT, tmp_path, config, manifest)
    assert report["status"] == "FAIL"
    assert report["errors"] and "mismatch" in report["errors"][0]
    assert report["checked_states"] == 0
    assert read_json(tmp_path / "verification.json")["status"] == "FAIL"


def test_protected_reference_change_blocks_all_new_gates(
    small_suite: dict[str, FrozenReference],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verification,
        "verify_reference_integrity",
        lambda root: {"unchanged": False, "changed": ["frozen-reference"]},
    )
    report = verify_m4(ROOT, tmp_path, M4Config())
    assert report["status"] == "FAIL" and not report["operator_equivalence"]
    assert "integrity failed" in report["errors"][0]


def test_density_failure_is_visible_and_never_becomes_pass(
    small_suite: dict[str, FrozenReference],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def nonphysical(*args: object, **kwargs: object) -> None:
        raise ValueError("Density matrix is not positive semidefinite")

    monkeypatch.setattr(verification, "simulate_density", nonphysical)
    report = verify_m4(ROOT, tmp_path, M4Config())
    assert report["status"] == "FAIL"
    assert not report["zero_noise_equivalence_verified"]
    assert "positive semidefinite" in report["errors"][0]


def test_suite_budget_failure_blocks_training_and_density_work(
    small_suite: dict[str, FrozenReference],
    tmp_path: Path,
) -> None:
    report = verify_m4(ROOT, tmp_path, M4Config(max_training_runs=1))
    assert report["status"] == "FAIL"
    assert "training_runs exceeds" in report["errors"][0]
    assert report["quality_training_performed"] is False


def test_changed_frozen_manifest_input_blocks_execution(
    small_suite: dict[str, FrozenReference],
    tmp_path: Path,
) -> None:
    config = M4Config(depths=(1,), restart_seeds=(0,))
    frozen_input = tmp_path / "input.json"
    frozen_input.write_text("before\n", encoding="utf-8")
    manifest = _manifest(config, small_suite)
    manifest["inputs"] = {str(frozen_input): lf_file_hash(frozen_input)}
    _seal(manifest)
    frozen_input.write_text("after\n", encoding="utf-8")
    report = verify_m4(ROOT, tmp_path, config, manifest)
    assert report["status"] == "FAIL"
    assert "input hash mismatch" in report["errors"][0]
    assert report["checked_states"] == 0


def test_empty_reference_suite_never_passes_vacuously(
    small_suite: dict[str, FrozenReference],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verification, "load_m4_references", lambda root: {})
    report = verify_m4(ROOT, tmp_path, M4Config())
    assert report["status"] == "FAIL"
    assert "suite is empty" in report["errors"][0]


def test_historical_audit_probabilities_are_compared_to_frozen_raw_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Recheck historical point probabilities, without reconstructing 660 states.
    integrity = {
        "m3_reference_commit": read_json(ROOT / "data/synthetic/m4/provenance.json")[
            "m3_reference_commit"
        ]
    }
    result = verification._historical_audits(ROOT, integrity)
    assert result["m3_saved_rows_rechecked"] == 660
    original_reader = verification.read_json
    semantics = copy.deepcopy(read_json(ROOT / "results/m4/metric_semantics_audit.json"))
    semantics["rows"][0]["p_schedule_optimal"] += 0.1

    def modified_reader(path: Path) -> Any:
        if path.name == "metric_semantics_audit.json":
            return semantics
        return original_reader(path)

    monkeypatch.setattr(verification, "read_json", modified_reader)
    with pytest.raises(ValueError, match="changes a saved M3 probability"):
        verification._historical_audits(ROOT, integrity)


def test_historical_audit_from_another_commit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    original_reader = verification.read_json
    m3 = copy.deepcopy(read_json(ROOT / "results/m4/preflight/m3_reverification.json"))
    m3["m3_reference_commit"] = "different"

    def modified_reader(path: Path) -> Any:
        if path.name == "m3_reverification.json":
            return m3
        return original_reader(path)

    monkeypatch.setattr(verification, "read_json", modified_reader)
    with pytest.raises(ValueError, match="disagrees with frozen M3 provenance"):
        verification._historical_audits(ROOT, {"m3_reference_commit": "a" * 40})
