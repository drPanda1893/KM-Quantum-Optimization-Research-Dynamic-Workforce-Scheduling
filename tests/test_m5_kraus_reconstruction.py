"""Numerical channel regressions for pinned Aer, without provider access."""

import json
import socket
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Choi, Kraus, SuperOp
from qiskit_aer import AerSimulator
from qiskit_aer.library import SaveDensityMatrix, SetDensityMatrix
from qiskit_aer.noise import (
    NoiseModel,
    QuantumError,
    amplitude_damping_error,
    coherent_unitary_error,
    mixed_unitary_error,
    pauli_error,
    thermal_relaxation_error,
)
from qiskit_aer.noise.errors import depolarizing_error

from km_quantum.m4.io import canonical_hash
from km_quantum.m4.noise import apply_readout, check_density_matrix
from km_quantum.m5.device_noise import build_device_noise_prediction, remap_backend_noise
from km_quantum.m5.snapshots import SyntheticBackend, snapshot_backend

CHANNEL_ATOL = 1e-12
ACTUAL_FIXTURE = Path(__file__).parent / "fixtures/m5/ibm_fez_kraus_channel_20261012.json"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline channel regression attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def _complex_unitary() -> np.ndarray:
    return np.array([[1, -1j], [-1j, 1]], dtype=complex) / np.sqrt(2)


def _complex_nonunitary() -> QuantumError:
    damping = [np.diag([1, np.sqrt(0.87)]), np.array([[0, np.sqrt(0.13)], [0, 0]])]
    return QuantumError(Kraus([_complex_unitary() @ matrix for matrix in damping]))


def _thermal_two_qubit() -> QuantumError:
    first = thermal_relaxation_error(100e-6, 150e-6, 280e-9)
    second = thermal_relaxation_error(120e-6, 180e-6, 280e-9)
    return depolarizing_error(0.008, 2).compose(second.tensor(first))


def _small_mixture() -> QuantumError:
    # This term is below Aer's C++ mixture cutoff.  It is still real probability
    # mass and must not disappear during an intermediate Choi eigendecomposition.
    small = 5e-11
    return pauli_error([("I", 0.5 - small), ("X", small), ("Y", 0.25), ("Z", 0.25)])


def _actual_error() -> QuantumError:
    fixture = json.loads(ACTUAL_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["source"] == "actual_ibm_backend_local_noise_model_from_readonly_calibration"
    assert fixture["hardware_measurement"] is False
    encoded = fixture["serialized_error"]
    assert canonical_hash(encoded) == fixture["serialized_error_sha256"]
    numerical = deepcopy(encoded)
    # This is the fixture's explicit complex encoding, not a generic reshape or
    # a production decoder. Scalars and metadata outside matrix params stay intact.
    for circuit in numerical["instructions"]:
        for instruction in circuit:
            if instruction["name"] in {"kraus", "unitary"}:
                matrices = []
                for values in instruction["params"]:
                    pairs = np.asarray(values, dtype=float)
                    assert pairs.ndim == 3 and pairs.shape[-1] == 2
                    assert pairs.shape[0] == pairs.shape[1]
                    matrices.append(pairs[:, :, 0] + 1j * pairs[:, :, 1])
                instruction["params"] = matrices
    error = QuantumError.from_dict(numerical)
    pairs = np.asarray(fixture["expected_superop_complex_pairs"])
    np.testing.assert_allclose(
        SuperOp(error).data, pairs[:, :, 0] + 1j * pairs[:, :, 1], rtol=0, atol=CHANNEL_ATOL
    )
    return error


CHANNEL_FACTORIES: tuple[tuple[str, Callable[[], QuantumError]], ...] = (
    ("amplitude_damping", lambda: amplitude_damping_error(0.13)),
    ("complex_nonunitary", _complex_nonunitary),
    ("complex_unitary", lambda: coherent_unitary_error(_complex_unitary())),
    (
        "mixed_complex_unitaries",
        lambda: mixed_unitary_error([(_complex_unitary(), 0.6), (np.array([[0, 1], [1, 0]]), 0.4)]),
    ),
    ("thermal_kraus", lambda: thermal_relaxation_error(100e-6, 150e-6, 35e-9)),
    ("thermal_reset_control", lambda: thermal_relaxation_error(100e-6, 75e-6, 35e-9)),
    ("combined_cz_thermal", _thermal_two_qubit),
    ("small_nonzero_mixture", _small_mixture),
)


def _model(error: QuantumError, gate: str | None = None) -> tuple[NoiseModel, tuple[int, ...]]:
    physical = (7,) if error.num_qubits == 1 else (7, 19)
    selected_gate = gate or ("sx" if error.num_qubits == 1 else "cz")
    model = NoiseModel()
    model.add_quantum_error(error, selected_gate, physical)
    return model, physical


def _reconstructed(model: NoiseModel) -> QuantumError:
    errors = [row for row in model.to_dict(serializable=False)["errors"] if row["type"] == "qerror"]
    assert len(errors) == 1
    return QuantumError.from_dict(errors[0])


def _assert_equal_cptp(before: QuantumError, after: QuantumError) -> None:
    np.testing.assert_allclose(SuperOp(after).data, SuperOp(before).data, rtol=0, atol=CHANNEL_ATOL)
    before_choi, after_choi = Choi(before).data, Choi(after).data
    np.testing.assert_allclose(after_choi, before_choi, rtol=0, atol=CHANNEL_ATOL)
    assert np.linalg.eigvalsh((after_choi + after_choi.conj().T) / 2).min() >= -CHANNEL_ATOL
    assert SuperOp(after).is_cptp(atol=CHANNEL_ATOL, rtol=0)
    dimension = 2**after.num_qubits
    # Qiskit's generic Kraus(SuperOp) conversion itself removes tiny eigenmodes.
    # Trace the Choi output subsystem directly, so the TP test introduces no
    # representation conversion or truncation of the channel being checked.
    traced_output = np.trace(
        after_choi.reshape(dimension, dimension, dimension, dimension), axis1=1, axis2=3
    )
    np.testing.assert_allclose(traced_output, np.eye(dimension), rtol=0, atol=CHANNEL_ATOL)


@pytest.mark.parametrize(
    "name,factory", CHANNEL_FACTORIES, ids=[row[0] for row in CHANNEL_FACTORIES]
)
def test_remap_preserves_superoperator_choi_and_cptp(
    name: str, factory: Callable[[], QuantumError]
) -> None:
    original = factory()
    full, physical = _model(original)
    declared_before = canonical_hash(full.to_dict(serializable=True))
    # In-memory round-trip is a control, independent of the project remapper.
    _assert_equal_cptp(original, _reconstructed(full))
    compact = remap_backend_noise(full, tuple(reversed(physical)))
    _assert_equal_cptp(original, _reconstructed(compact.quantum_model))
    gate = "sx" if len(physical) == 1 else "cz"
    assert compact.quantum_attachments == frozenset({(gate, tuple(reversed(range(len(physical)))))})
    assert "measure" not in compact.quantum_model.noise_instructions
    assert canonical_hash(full.to_dict(serializable=True)) == declared_before


@pytest.mark.parametrize("factory", [_complex_nonunitary, _thermal_two_qubit])
def test_complex_json_encoding_is_distinct_from_native_channel_data(
    factory: Callable[[], QuantumError],
) -> None:
    model, _ = _model(factory())
    native = model.to_dict(serializable=False)["errors"][0]
    encoded = model.to_dict(serializable=True)["errors"][0]
    numerical = next(
        item for circuit in native["instructions"] for item in circuit if item["name"] == "kraus"
    )
    json_matrix = next(
        item for circuit in encoded["instructions"] for item in circuit if item["name"] == "kraus"
    )
    assert np.asarray(numerical["params"][0]).shape == (2, 2)
    assert np.asarray(json_matrix["params"][0]).shape == (2, 2, 2)
    # The pinned external API does not decode AerJSONEncoder's complex pairs.
    # Production must therefore retain native numerical arrays for reconstruction.
    with pytest.raises(Exception, match="Input shape is not 1 or 2-dimensional"):
        QuantumError.from_dict(encoded)


def test_actual_readonly_snapshot_channel_has_equal_numerical_remap() -> None:
    fixture = json.loads(ACTUAL_FIXTURE.read_text(encoding="utf-8"))
    with pytest.raises(Exception, match="Input shape is not 1 or 2-dimensional"):
        QuantumError.from_dict(fixture["serialized_error"])
    original = _actual_error()
    model = NoiseModel()
    model.add_quantum_error(original, "id", [0])
    compact = remap_backend_noise(model, (0,))
    _assert_equal_cptp(original, _reconstructed(compact.quantum_model))
    assert compact.quantum_attachments == frozenset({("id", (0,))})


def _density(circuit: QuantumCircuit, model: NoiseModel) -> np.ndarray:
    saved = circuit.copy()
    saved.append(SaveDensityMatrix(saved.num_qubits, label="rho"), saved.qubits)
    result = (
        AerSimulator(
            method="density_matrix",
            noise_model=model,
            max_memory_mb=512,
            max_parallel_threads=2,
            max_parallel_experiments=1,
            max_parallel_shots=1,
            fusion_enable=False,
            enable_truncation=False,
            zero_threshold=1e-14,
        )
        .run(saved, shots=1, seed_simulator=0)
        .result()
    )
    assert result.success
    return np.asarray(result.data(0)["rho"].data, dtype=complex)


@pytest.mark.parametrize(
    "factory", [_complex_nonunitary, _thermal_two_qubit, _actual_error, _small_mixture]
)
def test_direct_kraus_state_application_matches_aer_with_ordered_physical_pairs(
    factory: Callable[[], QuantumError],
) -> None:
    error = factory()
    full, physical = _model(error, "unitary")
    compact = remap_backend_noise(full, tuple(reversed(physical)))
    dimension = 2**error.num_qubits
    state = np.arange(1, dimension + 1) + 1j * np.arange(dimension, 0, -1)
    state /= np.linalg.norm(state)
    rho = np.outer(state, state.conj())
    # Local channel coordinate j maps to compact qubit n-1-j.  Construct the
    # permutation independently of the production remapper and Qiskit evolution.
    permutation = np.zeros((dimension, dimension))
    for local in range(dimension):
        global_index = sum(
            ((local >> bit) & 1) << (error.num_qubits - 1 - bit) for bit in range(error.num_qubits)
        )
        permutation[global_index, local] = 1
    expected = np.zeros_like(rho)
    for probability, circuit in zip(error.probabilities, error.circuits, strict=True):
        for matrix in Kraus(circuit).data:
            global_matrix = permutation @ matrix @ permutation.T
            expected += probability * global_matrix @ rho @ global_matrix.conj().T
    circuit = QuantumCircuit(error.num_qubits)
    circuit.append(SetDensityMatrix(rho), circuit.qubits)
    circuit.unitary(np.eye(dimension), list(reversed(range(error.num_qubits))))
    actual = _density(circuit, compact.quantum_model)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=CHANNEL_ATOL)
    _, physicality = check_density_matrix(actual, error.num_qubits)
    assert physicality["verified"]


def test_backend_thermal_kraus_prediction_keeps_readout_once_and_circuit_frozen() -> None:
    backend = SyntheticBackend(two_qubit_gate="cz")
    properties = backend.target.qubit_properties
    for physical in backend.physical_subset[:2]:
        properties[physical].t2 = 1.5 * properties[physical].t1
    backend.target.qubit_properties = properties
    snapshot = snapshot_backend(backend, backend.physical_subset)
    circuit = QuantumCircuit(2)
    circuit.sx(1)
    circuit.cz(1, 0)
    circuit.x(0)
    frozen = circuit.copy()
    prediction = build_device_noise_prediction(backend, circuit, (4, 1), (1, 0), snapshot=snapshot)
    assert circuit == frozen
    assert prediction["status"] == "OFFLINE_READY" and prediction["hardware_used"] is False
    assert prediction["density_physicality"]["verified"]
    assert prediction["coverage"]["quantum_channel_applications"] == 3
    assert prediction["coverage"]["readout_in_density_model"] is False
    assert prediction["readout_applications"] == 1
    expected = apply_readout(
        prediction["probabilities_before_readout"], prediction["readout_matrices_m2_order"]
    )
    np.testing.assert_allclose(
        prediction["probabilities_after_readout"], expected, rtol=0, atol=1e-12
    )
    assert prediction["noise_model_options"]["thermal_relaxation"] is True
    assert prediction["additional_m4_depolarization"] is False
