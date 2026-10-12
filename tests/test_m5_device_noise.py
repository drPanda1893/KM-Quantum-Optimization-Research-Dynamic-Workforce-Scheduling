"""Independent physical channel fixtures and explicit offline device predictions."""

from copy import deepcopy

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector
from qiskit_aer import AerSimulator
from qiskit_aer.library import SaveDensityMatrix
from qiskit_aer.noise import NoiseModel, ReadoutError, depolarizing_error, pauli_error

from km_quantum.m5.device_noise import build_device_noise_prediction, remap_backend_noise
from km_quantum.m5.snapshots import SyntheticBackend, snapshot_backend


def _probabilities(circuit: QuantumCircuit, model: NoiseModel) -> np.ndarray:
    saved = circuit.copy()
    saved.append(SaveDensityMatrix(saved.num_qubits, label="rho"), saved.qubits)
    result = (
        AerSimulator(
            method="density_matrix",
            noise_model=model,
            fusion_enable=False,
            max_memory_mb=512,
            max_parallel_threads=2,
            max_parallel_experiments=1,
        )
        .run(saved, shots=1, seed_simulator=0)
        .result()
    )
    return np.real(np.diag(result.data(0)["rho"].data))


def test_qubit_specific_quantum_and_readout_errors_use_physical_ids() -> None:
    model = NoiseModel()
    model.add_quantum_error(depolarizing_error(0.2, 1), "x", [9])
    model.add_quantum_error(depolarizing_error(0.6, 1), "x", [22])
    model.add_quantum_error(depolarizing_error(0.9, 1), "x", [45])
    model.add_readout_error(ReadoutError([[0.9, 0.1], [0.3, 0.7]]), [9])
    model.add_readout_error(ReadoutError([[0.8, 0.2], [0.4, 0.6]]), [22])
    compact = remap_backend_noise(model, (22, 9))
    circuit = QuantumCircuit(2)
    circuit.x(0)
    circuit.x(1)
    # q0 physical22: [.3,.7]; q1 physical9: [.1,.9].  The high unused
    # physical45 error must neither attach to q0 nor widen the simulation.
    np.testing.assert_allclose(
        _probabilities(circuit, compact.quantum_model), [0.03, 0.07, 0.27, 0.63]
    )
    np.testing.assert_array_equal(compact.readout_by_physical[9], [[0.9, 0.1], [0.3, 0.7]])
    assert set(compact.quantum_model.noise_qubits) == {0, 1}
    assert "measure" not in compact.quantum_model.noise_instructions


def test_directed_native_ecr_error_preserves_qarg_order_and_local_channel_coordinates() -> None:
    model = NoiseModel()
    model.add_quantum_error(pauli_error([("IX", 1)]), "ecr", [22, 9])
    compact = remap_backend_noise(model, (9, 22))
    assert compact.quantum_attachments == frozenset({("ecr", (1, 0))})
    forward = QuantumCircuit(2)
    forward.ry(0.43, 0)
    forward.ecr(1, 0)
    expected = forward.copy()
    # IX flips error-local qubit0, the first ECR qarg physical22=compact1.
    expected.x(1)
    np.testing.assert_allclose(
        _probabilities(forward, compact.quantum_model),
        Statevector.from_instruction(expected).probabilities(),
    )
    reverse = QuantumCircuit(2)
    reverse.ry(0.43, 0)
    reverse.ecr(0, 1)
    np.testing.assert_allclose(
        _probabilities(reverse, compact.quantum_model),
        Statevector.from_instruction(reverse).probabilities(),
    )
    assert "cx" not in compact.quantum_model.noise_instructions


def test_measured_subset_mapping_readout_once_and_wide_backend_stays_compact() -> None:
    backend = SyntheticBackend()
    snapshot = snapshot_backend(backend, backend.physical_subset)
    circuit = QuantumCircuit(3)
    # Compact2 physical9 is M2 bit0; compact0 physical1 is M2 bit1.
    circuit.rz(0.4, 1)
    result = build_device_noise_prediction(backend, circuit, (1, 4, 9), (2, 0), snapshot=snapshot)
    assert result["status"] == "OFFLINE_READY"
    assert result["active_simulated_physical_width"] == 3
    assert result["logical_register_width"] == 2
    assert len(result["probabilities_before_readout"]) == 4
    np.testing.assert_allclose(result["probabilities_before_readout"], [1, 0, 0, 0])
    q9 = 0.01 + (9 % 7) * 0.001
    q1 = 0.01 + (1 % 7) * 0.001
    expected = [(1 - q9) * (1 - q1), q9 * (1 - q1), (1 - q9) * q1, q9 * q1]
    np.testing.assert_allclose(result["probabilities_after_readout"], expected, atol=1e-14)
    assert result["readout_applications"] == 1
    assert result["density_physicality"]["readout_applied"] is False
    assert result["coverage"]["readout_in_density_model"] is False
    assert result["additional_m4_depolarization"] is False
    assert not result["device_calibration_used"] and not result["hardware_used"]
    assert result["simulation_options"]["max_memory_mb"] == 512
    assert result["simulation_options"]["max_parallel_threads"] == 2
    assert not result["idle_delay_effects_modelled"]


def test_nonzero_routing_wire_is_marginalized_and_asymmetric_readout_orientation() -> None:
    backend = SyntheticBackend()
    # Explicitly calibrated ideal Xs isolate marginalization and readout from
    # gate channels.  The populated compact1 boundary wire cannot be cut out of
    # the state before simulation, but is not measured as part of M2.
    for physical in (4, 9):
        backend.target["x"][(physical,)].duration = 0
        backend.target["x"][(physical,)].error = 0
    snapshot = snapshot_backend(backend, backend.physical_subset)
    circuit = QuantumCircuit(3)
    circuit.x(1)
    circuit.x(2)
    result = build_device_noise_prediction(backend, circuit, (1, 4, 9), (2, 0), snapshot=snapshot)
    np.testing.assert_allclose(result["probabilities_before_readout"], [0, 1, 0, 0])
    p10_q9 = 0.025 + (9 % 5) * 0.001
    p01_q1 = 0.01 + (1 % 7) * 0.001
    np.testing.assert_allclose(
        result["probabilities_after_readout"],
        [
            p10_q9 * (1 - p01_q1),
            (1 - p10_q9) * (1 - p01_q1),
            p10_q9 * p01_q1,
            (1 - p10_q9) * p01_q1,
        ],
    )
    assert result["readout_applications"] == 1
    assert result["coverage"]["quantum_channel_applications"] == 0


@pytest.mark.parametrize("missing", ["error", "duration", "t1", "t2", "readout"])
def test_missing_relevant_calibration_blocks_instead_of_perfect_fallback(missing: str) -> None:
    backend = SyntheticBackend()
    circuit = QuantumCircuit(1)
    circuit.x(0)
    physical = backend.physical_subset[0]
    if missing in {"error", "duration"}:
        setattr(backend.target["x"][(physical,)], missing, None)
    elif missing in {"t1", "t2"}:
        # The public Target getter returns a copied qubit-properties sequence.
        qubit_properties = backend.target.qubit_properties
        setattr(qubit_properties[physical], missing, None)
        backend.target.qubit_properties = qubit_properties
    else:
        backend.target["measure"][(physical,)].error = None
    snapshot = snapshot_backend(backend, backend.physical_subset)
    result = build_device_noise_prediction(backend, circuit, (physical,), (0,), snapshot=snapshot)
    assert result["status"] == "BLOCKED"
    assert result["probabilities_before_readout"] is None
    assert result["probabilities_after_readout"] is None
    assert result["coverage"]["missing_calibrations"]


def test_active_gate_exposure_native_instruction_names_and_full_snapshot_stamp() -> None:
    backend = SyntheticBackend(two_qubit_gate="cz")
    physical = backend.physical_subset[:2]
    circuit = QuantumCircuit(2)
    circuit.sx(0)
    circuit.cz(0, 1)
    circuit.x(1)
    snapshot = snapshot_backend(backend, backend.physical_subset)
    result = build_device_noise_prediction(backend, circuit, physical, (1, 0), snapshot=snapshot)
    assert result["coverage"]["verified"]
    assert result["coverage"]["quantum_channel_applications"] == 3
    assert result["snapshot_retrieved_at"] == snapshot["retrieved_at"]
    assert result["snapshot_calibration_timestamp"] == snapshot["calibration_timestamp"]
    assert {row["name"] for row in result["coverage"]["gate_occurrences"]} == {"sx", "cz", "x"}


def test_stale_snapshot_and_native_reverse_direction_are_blocked() -> None:
    backend = SyntheticBackend()
    snapshot = snapshot_backend(backend, backend.physical_subset)
    stale = deepcopy(snapshot)
    next(row for row in stale["instructions"] if row["name"] == "x")["error"] = 0.3
    circuit = QuantumCircuit(1)
    circuit.x(0)
    result = build_device_noise_prediction(backend, circuit, (1,), (0,), snapshot=stale)
    assert result["status"] == "BLOCKED"
    reverse = QuantumCircuit(2)
    reverse.ecr(1, 0)
    result = build_device_noise_prediction(backend, reverse, (1, 4), (0, 1), snapshot=snapshot)
    assert result["status"] == "BLOCKED"


def test_delays_require_an_explicit_model_and_missing_readout_is_visible() -> None:
    backend = SyntheticBackend()
    snapshot = snapshot_backend(backend, backend.physical_subset)
    circuit = QuantumCircuit(1)
    circuit.delay(10, 0)
    result = build_device_noise_prediction(backend, circuit, (1,), (0,), snapshot=snapshot)
    assert result["status"] == "BLOCKED"
    assert result["idle_delay_effects_modelled"] is False


def test_synthetic_snapshot_cannot_be_relabeled_as_provider_calibration() -> None:
    backend = SyntheticBackend()
    snapshot = snapshot_backend(backend, backend.physical_subset)
    snapshot["source"] = "provider"
    with pytest.raises(ValueError, match="cannot claim device calibration"):
        build_device_noise_prediction(backend, QuantumCircuit(1), (1,), (0,), snapshot=snapshot)


def test_resource_and_measurement_guards_reject_before_backend_noise() -> None:
    backend = SyntheticBackend()
    snapshot = snapshot_backend(backend, backend.physical_subset)
    with pytest.raises(ValueError, match="one to ten"):
        build_device_noise_prediction(
            backend, QuantumCircuit(11), range(11), (0,), snapshot=snapshot
        )
    with pytest.raises(ValueError, match="unmeasured"):
        build_device_noise_prediction(
            backend, QuantumCircuit(1).measure_all(inplace=False), (1,), (0,), snapshot=snapshot
        )
    with pytest.raises(ValueError, match="distinct compact"):
        build_device_noise_prediction(backend, QuantumCircuit(2), (1, 4), (0, 0), snapshot=snapshot)
    with pytest.raises(ValueError, match="local physical"):
        model = NoiseModel()
        model.add_all_qubit_quantum_error(depolarizing_error(0.1, 1), "x")
        remap_backend_noise(model, (1,))
