"""Allowlisted device facts, explicit missing calibration, and raw QPY hashing."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.circuit import IfElseOp
from qiskit.transpiler import Target

from km_quantum.m5.circuits import compile_hardware, compiled_ideal_probabilities
from km_quantum.m5.snapshots import (
    SYNTHETIC_TIMESTAMP,
    SyntheticBackend,
    load_qpy,
    restrict_target,
    snapshot_backend,
    write_qpy,
)


def test_synthetic_target_snapshot_is_reproducible_asymmetric_and_explicitly_offline() -> None:
    backend = SyntheticBackend()
    backend.secret_token = "must-never-be-exported"
    first = snapshot_backend(backend, backend.physical_subset, retrieved_at=SYNTHETIC_TIMESTAMP)
    second = snapshot_backend(
        SyntheticBackend(), backend.physical_subset, retrieved_at=SYNTHETIC_TIMESTAMP
    )
    assert first == second
    assert first["source"] == "offline_synthetic" and not first["hardware_used"]
    assert first["calibration_timestamp"] == SYNTHETIC_TIMESTAMP
    assert "test-only" in first["timestamp_definition"]
    assert "must-never-be-exported" not in json.dumps(first)
    q1 = first["qubits"][0]
    assert q1["physical_id"] == 1 and q1["t1_seconds"] > 0
    assert q1["readout_matrix"][0][1] != q1["readout_matrix"][1][0]
    assert q1["readout_matrix_source"] == "target_asymmetric_probabilities"
    assert all(not row["missing"] for row in first["instructions"])
    with pytest.raises(ValueError, match="labeled"):
        snapshot_backend(backend, backend.physical_subset, source="provider")
    with pytest.raises(RuntimeError, match="cannot submit"):
        backend.run(QuantumCircuit(1))


def test_missing_calibration_values_are_not_silently_perfect() -> None:
    backend = SyntheticBackend()
    target = Target(num_qubits=53)
    for name in backend.target.operation_names:
        target.add_instruction(
            backend.target.operation_from_name(name),
            {qubits: None for qubits in backend.target[name]},
        )
    backend._target = target
    backend.calibration_timestamp = None
    snapshot = snapshot_backend(backend, backend.physical_subset)
    assert snapshot["missing"] == ["calibration_timestamp"]
    assert all(
        row["duration_seconds"] is None
        and row["error"] is None
        and row["missing"] == ["duration_seconds", "error"]
        for row in snapshot["instructions"]
    )
    assert all(
        row["readout_matrix"] is None and "readout_matrix" in row["missing"]
        for row in snapshot["qubits"]
    )


def test_subset_must_be_seven_connected_valid_physical_ids() -> None:
    backend = SyntheticBackend()
    for subset in ((1, 4), (1, 4, 9, 13, 22, 31, 50), (1, 4, 9, 13, 22, 31, 128)):
        with pytest.raises(ValueError):
            restrict_target(backend.target, subset)


def test_unused_control_flow_target_features_do_not_block_static_compilation() -> None:
    backend = SyntheticBackend()
    backend.target.add_instruction(IfElseOp, name="if_else")
    restricted = restrict_target(backend.target, backend.physical_subset)
    assert "if_else" not in restricted.operation_names
    logical = QuantumCircuit(2)
    logical.h(0)
    logical.cx(0, 1)
    compiled = compile_hardware(logical, backend.target, backend.physical_subset)
    assert "if_else" not in compiled.provider_circuit.count_ops()


@pytest.mark.parametrize("kind", ["fake_family", "aer_family", "configured_simulator"])
def test_fake_and_simulator_metadata_never_claims_provider_hardware(kind: str) -> None:
    fixture = SyntheticBackend()
    if kind == "fake_family":
        backend_type = type(
            "CalibrationFixture",
            (),
            {
                "__module__": "qiskit_ibm_runtime.fake_provider.fixture",
            },
        )
    elif kind == "aer_family":
        backend_type = type("AerSimulator", (), {"__module__": "qiskit_aer.backends"})
    else:
        backend_type = type(
            "ConfiguredBackend",
            (),
            {
                "configuration": lambda self: type("Configuration", (), {"simulator": True})(),
            },
        )
    backend = backend_type()
    backend.target = fixture.target
    backend.name = "explicit_offline_fixture"
    snapshot = snapshot_backend(backend, fixture.physical_subset)
    assert snapshot["source"] == "offline_test_backend"
    assert snapshot["synthetic_model_is_device_calibration"] is False
    assert not snapshot["hardware_used"]
    with pytest.raises(ValueError, match="provider hardware"):
        snapshot_backend(backend, fixture.physical_subset, source="provider")


def test_qpy_raw_byte_hash_roundtrip_and_overwrite_guard(tmp_path: Path) -> None:
    backend = SyntheticBackend()
    logical = QuantumCircuit(3)
    logical.x(0)
    logical.ry(0.47, 1)
    logical.cx(0, 2)
    compiled = compile_hardware(logical, backend.target, backend.physical_subset)
    path = tmp_path / "native.qpy"
    record = write_qpy(path, compiled.provider_circuit)
    assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert record["qiskit_version"] == "2.5.2" and record["qpy_version"] > 0
    assert not record["version_independent_format"]
    original_bytes = path.read_bytes()
    resumed = write_qpy(path, compiled.provider_circuit)
    assert path.read_bytes() == original_bytes
    assert resumed["sha256"] == record["sha256"]
    assert resumed["serialization_is_canonical"] is False
    loaded = load_qpy(path, expected_sha256=record["sha256"])
    assert loaded == compiled.provider_circuit
    assert (
        loaded.layout.final_index_layout() == compiled.provider_circuit.layout.final_index_layout()
    )
    np.testing.assert_allclose(compiled_ideal_probabilities(compiled).sum(), 1)
    with pytest.raises(ValueError, match="SHA256"):
        load_qpy(path, expected_sha256="0" * 64)
    changed = logical.copy()
    changed.x(2)
    with pytest.raises(ValueError, match="immutable"):
        write_qpy(path, changed)
