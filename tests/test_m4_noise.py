"""Independent channel/ordering fixtures and bounded deterministic compilation."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
from qiskit.quantum_info import Statevector
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, ReadoutError
from scipy.stats import binomtest

from km_quantum.m4 import noise
from km_quantum.m4.noise import (
    DEFAULT_NOISE_PROFILES,
    CompilerConfig,
    NoiseProfile,
    ResourceLimits,
    apply_readout,
    check_density_matrix,
    circuit_sequence_hash,
    compile_bound_circuit,
    evaluate_noise,
    gate_noise_model,
    multinomial_counts,
    noise_gate_coverage,
    resource_preflight,
    simulate_density,
    symmetric_readout_matrices,
)
from km_quantum.qaoa.references import load_references
from km_quantum.qaoa.simulation import bind_parameters, build_qaoa

ROOT = Path(__file__).resolve().parents[1]


def test_default_profile_contract_is_exactly_the_frozen_artificial_suite() -> None:
    assert [
        (item.name, item.lambda_1q, item.lambda_2q, item.readout_flip)
        for item in DEFAULT_NOISE_PROFILES
    ] == [
        ("ideal", 0, 0, 0),
        ("gate_only", 0.0005, 0.005, 0),
        ("readout_only", 0, 0, 0.01),
        ("combined_low", 0.0005, 0.005, 0.01),
        ("combined_high", 0.002, 0.02, 0.03),
    ]


def test_compilation_verifies_amplitudes_and_serializes_deterministic_sequence() -> None:
    circuit = QuantumCircuit(3)
    circuit.h(0)
    circuit.ry(0.71, 2)
    circuit.cx(2, 0)
    circuit.rzz(-0.33, 0, 1)
    circuit.rx(0.19, 1)
    circuit.global_phase = 0.31
    first = compile_bound_circuit(circuit)
    second = compile_bound_circuit(circuit.copy())
    assert first.sequence_hash == second.sequence_hash
    assert first.metadata["gate_sequence"] == second.metadata["gate_sequence"]
    assert first.metadata["compiler"] == {
        "seed": 424242,
        "optimization_level": 0,
        "basis_gates": ["rz", "sx", "x", "cx"],
    }
    assert set(first.metadata["gate_histogram"]) <= {"rz", "sx", "x", "cx"}
    assert first.metadata["active_qubits"] == [0, 1, 2]
    assert first.metadata["gate_count"] == sum(first.metadata["gate_histogram"].values())
    assert first.metadata["gate_count"] == (
        first.metadata["one_qubit_gate_count"] + first.metadata["two_qubit_gate_count"]
    )
    assert first.metadata["max_amplitude_difference_up_to_global_phase"] < 1e-12
    assert Statevector.from_instruction(circuit).equiv(Statevector.from_instruction(first.circuit))
    assert not first.metadata["routing"] and first.metadata["layout"] is None
    json.dumps(first.metadata, allow_nan=False)
    assert json.loads(json.dumps(first.metadata)) == first.metadata
    np.testing.assert_array_equal(first.ideal_probabilities, second.ideal_probabilities)
    assert not first.ideal_probabilities.flags.writeable


@pytest.mark.parametrize("name", ["A_single_job", "K_bounded_slack", "M_active_imbalance"])
@pytest.mark.parametrize("p", [1, 2])
def test_zero_noise_full_density_matches_frozen_m3_statevector(name: str, p: int) -> None:
    reference = load_references(ROOT)[name]
    prepared = build_qaoa(reference.ising, p, max_qubits=10)
    bound = bind_parameters(prepared, np.linspace(0.13, 0.79, len(prepared.parameters)))
    compiled = compile_bound_circuit(bound)
    result = simulate_density(compiled, DEFAULT_NOISE_PROFILES[0])
    np.testing.assert_allclose(
        result.probabilities_before_readout,
        Statevector.from_instruction(bound).probabilities(),
        atol=1e-10,
    )
    assert result.physicality["zero_noise_statevector_equivalence_verified"]
    assert result.physicality["zero_noise_density_max_absolute_difference"] < 1e-10
    assert result.physicality["verified"]
    assert result.backend_metadata["options"]["enable_truncation"] is False
    assert result.backend_metadata["options"]["max_parallel_threads"] == 2
    assert not result.backend_metadata["measurement_present"]
    assert not result.backend_metadata["readout_present"]


def test_one_qubit_depolarization_uses_the_api_mixture_parameter() -> None:
    circuit = QuantumCircuit(1)
    circuit.x(0)
    result = simulate_density(compile_bound_circuit(circuit), NoiseProfile("fixture", 0.2, 0, 0))
    # (1-lambda)|1><1| + lambda*I/2; lambda is not total nonidentity Pauli mass.
    np.testing.assert_allclose(result.probabilities_before_readout, [0.1, 0.9], atol=1e-12)
    assert result.coverage["one_qubit_channel_applications"] == 1
    assert result.coverage["two_qubit_channel_applications"] == 0
    assert result.physicality["minimum_eigenvalue"] == pytest.approx(0.1)


def test_two_qubit_depolarization_follows_basis_index_and_dimension() -> None:
    circuit = QuantumCircuit(2)
    circuit.x(0)
    circuit.cx(0, 1)
    result = simulate_density(compile_bound_circuit(circuit), NoiseProfile("fixture", 0, 0.4, 0))
    # Ideal index 3; (1-lambda)|11><11| + lambda*I/4.
    np.testing.assert_allclose(result.probabilities_before_readout, [0.1, 0.1, 0.1, 0.7])
    assert result.coverage["two_qubit_channel_applications"] == 1
    assert result.coverage["one_qubit_channel_applications"] == 0


def test_state_preparation_gates_are_exposed_to_noise() -> None:
    circuit = QuantumCircuit(1)
    circuit.ry(np.pi / 3, 0)
    compiled = compile_bound_circuit(circuit)
    assert compiled.metadata["gate_histogram"]["sx"] == 2
    result = simulate_density(compiled, NoiseProfile("preparation", 0.2, 0, 0))
    # Two depolarizers commute with all 1q gates: Bloch contraction=(1-.2)^2.
    np.testing.assert_allclose(result.probabilities_before_readout, [0.66, 0.34])
    assert result.coverage["state_preparation_included"]
    assert result.coverage["one_qubit_channel_applications"] == 2
    assert result.coverage["active_noisy_instruction_names"] == ["sx"]


def test_missing_gate_names_in_noise_model_are_rejected() -> None:
    circuit = QuantumCircuit(2)
    circuit.x(0)
    circuit.cx(0, 1)
    profile = NoiseProfile("coverage", 0.01, 0.02, 0)
    with pytest.raises(ValueError, match="does not cover"):
        noise_gate_coverage(circuit, profile, NoiseModel())
    model = gate_noise_model(profile)
    model.add_all_qubit_readout_error(ReadoutError([[0.9, 0.1], [0.2, 0.8]]))
    with pytest.raises(ValueError, match="Readout must not"):
        noise_gate_coverage(circuit, profile, model)


def test_frozen_circuit_mutation_between_profiles_is_rejected() -> None:
    circuit = QuantumCircuit(1)
    circuit.h(0)
    compiled = compile_bound_circuit(circuit)
    digest = compiled.sequence_hash
    for profile in DEFAULT_NOISE_PROFILES:
        simulate_density(compiled, profile)
        assert circuit_sequence_hash(compiled.circuit) == digest
    compiled.circuit.x(0)
    with pytest.raises(ValueError, match="was mutated"):
        simulate_density(compiled, DEFAULT_NOISE_PROFILES[0])


def test_asymmetric_readout_orientation_exposes_transposition_error() -> None:
    matrix = [[0.9, 0.1], [0.3, 0.7]]
    np.testing.assert_allclose(apply_readout([0, 1], [matrix]), [0.3, 0.7])
    np.testing.assert_allclose(apply_readout([0.25, 0.75], [matrix]), [0.45, 0.55])


def test_two_qubit_asymmetric_readout_uses_qiskit_little_endian_indices() -> None:
    q0 = [[0.9, 0.1], [0.3, 0.7]]
    q1 = [[0.8, 0.2], [0.4, 0.6]]
    # True index1 is q0=1,q1=0; flat order is observed 00,01,10,11.
    np.testing.assert_allclose(apply_readout([0, 1, 0, 0], [q0, q1]), [0.24, 0.56, 0.06, 0.14])
    # Explicit enumeration is independent of the tensor-axis implementation.
    original = np.array([0.12, 0.23, 0.34, 0.31])
    expected = np.zeros(4)
    for true in range(4):
        for observed in range(4):
            expected[observed] += (
                original[true]
                * q0[true & 1][observed & 1]
                * q1[(true >> 1) & 1][(observed >> 1) & 1]
            )
    np.testing.assert_allclose(apply_readout(original, [q0, q1]), expected, atol=1e-14)


@pytest.mark.parametrize("number_qubits", [1, 2])
def test_analytical_readout_agrees_with_seeded_aer_counts(number_qubits: int) -> None:
    circuit = QuantumCircuit(number_qubits)
    circuit.ry(0.73, 0)
    if number_qubits == 2:
        circuit.ry(1.19, 1)
        circuit.cx(0, 1)
    compiled = compile_bound_circuit(circuit)
    matrices = (
        np.array([[0.93, 0.07], [0.21, 0.79]]),
        np.array([[0.84, 0.16], [0.03, 0.97]]),
    )[:number_qubits]
    expected = apply_readout(compiled.ideal_probabilities, matrices)
    model = NoiseModel()
    for qubit, matrix in enumerate(matrices):
        model.add_readout_error(ReadoutError(matrix.tolist()), [qubit])
    measured = compiled.circuit.measure_all(inplace=False)
    simulator = AerSimulator(
        method="density_matrix",
        noise_model=model,
        max_parallel_threads=2,
        max_parallel_experiments=1,
        max_parallel_shots=1,
    )
    shots = 65536
    counts = simulator.run(measured, shots=shots, seed_simulator=97531).result().get_counts()
    assert sum(counts.values()) == shots
    for index, probability in enumerate(expected):
        count = counts.get(format(index, f"0{number_qubits}b"), 0)
        assert binomtest(count, shots, float(probability)).pvalue >= 1e-9
        interval = binomtest(count, shots).proportion_ci(confidence_level=1 - 1e-9)
        assert interval.low <= probability <= interval.high


def test_separate_readout_does_not_change_density_or_apply_twice() -> None:
    circuit = QuantumCircuit(1)
    circuit.x(0)
    compiled = compile_bound_circuit(circuit)
    true, observed, metadata = evaluate_noise(compiled, NoiseProfile("readout", 0, 0, 0.1))
    np.testing.assert_allclose(true, [0, 1], atol=1e-12)
    np.testing.assert_allclose(observed, [0.1, 0.9], atol=1e-12)
    assert metadata["readout_applications"] == 1
    assert metadata["density_physicality"]["readout_applied"] is False
    assert metadata["total_variation_distance_ideal_to_observed"] == pytest.approx(0.1)
    assert metadata["total_variation_distance_ideal_to_before_readout"] == pytest.approx(0)
    json.dumps(metadata, allow_nan=False)


def test_combined_density_and_asymmetric_readout_agree_with_aer_counts() -> None:
    circuit = QuantumCircuit(2)
    circuit.ry(0.87, 0)
    circuit.h(1)
    circuit.cx(0, 1)
    circuit.rx(-0.41, 1)
    compiled = compile_bound_circuit(circuit)
    profile = NoiseProfile("combined_fixture", 0.03, 0.07, 0)
    density = simulate_density(compiled, profile)
    matrices = (
        np.array([[0.91, 0.09], [0.23, 0.77]]),
        np.array([[0.87, 0.13], [0.04, 0.96]]),
    )
    expected = apply_readout(density.probabilities_before_readout, matrices)
    model = gate_noise_model(profile)
    for qubit, matrix in enumerate(matrices):
        model.add_readout_error(ReadoutError(matrix.tolist()), [qubit])
    simulator = AerSimulator(
        method="density_matrix",
        noise_model=model,
        max_parallel_threads=2,
        max_parallel_experiments=1,
        max_parallel_shots=1,
        fusion_enable=False,
    )
    shots = 65536
    counts = (
        simulator.run(
            compiled.circuit.measure_all(inplace=False), shots=shots, seed_simulator=71923
        )
        .result()
        .get_counts()
    )
    for index, probability in enumerate(expected):
        count = counts.get(format(index, "02b"), 0)
        assert binomtest(count, shots, float(probability)).pvalue >= 1e-9


@pytest.mark.parametrize(
    ("matrix", "error"),
    [
        (np.array([[1, 0], [0, 1]], dtype=complex), "trace"),
        (np.array([[0.5, 0.2j], [0.2j, 0.5]]), "Hermitian"),
        (np.array([[1.1, 0], [0, -0.1]], dtype=complex), "positive semidefinite"),
        (np.ones((3, 3), dtype=complex), "dimension"),
        (np.array([[1, np.nan], [0, 0]], dtype=complex), "nonfinite"),
    ],
)
def test_density_physicality_rejects_nonphysical_matrices(matrix: Any, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        check_density_matrix(matrix, 1)


def test_density_roundoff_adjustment_is_bounded_and_visible() -> None:
    probabilities, report = check_density_matrix(np.diag([1 + 1e-12, -1e-12]), 1)
    np.testing.assert_array_equal(probabilities, [1, 0])
    assert report["minimum_eigenvalue"] == pytest.approx(-1e-12)
    assert 0 < report["born_probability_max_roundoff_adjustment"] < 2e-12


def test_resource_preflight_enforces_memory_threads_and_suite_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        noise.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=16 * 1024**3, available=8 * 1024**3),
    )
    monkeypatch.setattr(noise.psutil, "cpu_count", lambda **kwargs: 16)
    report = resource_preflight(10, instance_count=12, training_runs=240, density_runs=1200)
    assert report["density_complex_entries"] == 4**10
    assert report["density_state_bytes"] == 16 * 1024**2
    assert report["estimated_workspace_bytes"] == 128 * 1024**2
    assert report["parallel_run_workers"] == 1
    for kwargs in ({"instance_count": 13}, {"training_runs": 241}, {"density_runs": 1201}):
        with pytest.raises(ValueError, match="run limit"):
            resource_preflight(1, **kwargs)
    with pytest.raises(ValueError, match="max_qubits"):
        resource_preflight(11)
    with pytest.raises(ValueError, match="workspace estimate exceeds"):
        resource_preflight(10, ResourceLimits(max_memory_mb=64))
    monkeypatch.setattr(
        noise.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=16 * 1024**3, available=16 * 1024**2),
    )
    with pytest.raises(ValueError, match="Insufficient available RAM"):
        resource_preflight(10)
    monkeypatch.setattr(noise.psutil, "cpu_count", lambda **kwargs: 1)
    with pytest.raises(ValueError, match="logical CPUs"):
        resource_preflight(1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_qubits": 11},
        {"max_parallel_threads": 3},
        {"max_memory_mb": 0},
        {"max_density_runs": 1201},
        {"max_instances": True},
    ],
)
def test_resource_caps_cannot_expand_automatically(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ResourceLimits(**kwargs)


def test_unbound_and_measured_circuits_are_rejected_before_simulation() -> None:
    circuit = QuantumCircuit(1)
    circuit.rx(Parameter("a"), 0)
    with pytest.raises(ValueError, match="fully bound"):
        compile_bound_circuit(circuit)
    circuit = QuantumCircuit(1)
    circuit.h(0)
    with pytest.raises(ValueError, match="classical bits"):
        compile_bound_circuit(circuit.measure_all(inplace=False))
    circuit = QuantumCircuit(1)
    circuit.reset(0)
    with pytest.raises(ValueError, match="unitary"):
        compile_bound_circuit(circuit)
    with pytest.raises(ValueError, match="optimization_level"):
        CompilerConfig(optimization_level=1)
    with pytest.raises(ValueError, match="basis_gates"):
        CompilerConfig(basis_gates=("h", "cx"))


def test_zero_qubit_circuit_and_readout_have_one_certain_outcome() -> None:
    compiled = compile_bound_circuit(QuantumCircuit(0))
    true, observed, _ = evaluate_noise(compiled, DEFAULT_NOISE_PROFILES[-1])
    np.testing.assert_array_equal(true, [1])
    np.testing.assert_array_equal(observed, [1])
    assert multinomial_counts(observed, shots=1024, seed=1) == {"": 1024}


def test_multinomial_sample_seed_order_normalization_and_uncertainty() -> None:
    probabilities = np.array([0.1, 0.2, 0.3, 0.4])
    first = multinomial_counts(probabilities, shots=1024, seed=105)
    assert first == multinomial_counts(probabilities, shots=1024, seed=105)
    assert first != multinomial_counts(probabilities, shots=1024, seed=106)
    assert sum(first.values()) == 1024
    assert set(first) == {"00", "01", "10", "11"}
    for displayed, count in first.items():
        expected = probabilities[int(displayed, 2)]
        assert binomtest(count, 1024, expected).pvalue >= 1e-9
    assert multinomial_counts([0, 1], shots=1024, seed=7) == {"1": 1024}
    for kwargs in ({"shots": 0, "seed": 0}, {"shots": 1, "seed": -1}):
        with pytest.raises(ValueError, match="integer"):
            multinomial_counts([1], **kwargs)


@pytest.mark.parametrize(
    "probabilities", [[], [0.1, 0.2, 0.7], [1.1, -0.1], [0.4, 0.4], [np.nan, 0]]
)
def test_invalid_probability_vectors_are_rejected(probabilities: list[float]) -> None:
    with pytest.raises(ValueError):
        multinomial_counts(probabilities, seed=0)


@pytest.mark.parametrize(
    "matrix", [[[0.9, 0.2], [0.1, 0.8]], [[1, 0, 0], [0, 1, 0]], [[1.1, -0.1], [0, 1]]]
)
def test_invalid_readout_matrices_are_rejected(matrix: list[list[float]]) -> None:
    with pytest.raises(ValueError, match="row-stochastic"):
        apply_readout([0, 1], [matrix])
    with pytest.raises(ValueError, match="per qubit"):
        apply_readout([0, 1], [])


def test_readout_identity_and_symmetric_profiles_preserve_normalization() -> None:
    vector = np.array([0.03, 0.17, 0.31, 0.49])
    np.testing.assert_allclose(apply_readout(vector, symmetric_readout_matrices(2, 0)), vector)
    uniform = np.full(4, 0.25)
    np.testing.assert_allclose(apply_readout(uniform, symmetric_readout_matrices(2, 0.3)), uniform)
    assert apply_readout(vector, symmetric_readout_matrices(2, 0.03)).sum() == pytest.approx(1)


@pytest.mark.parametrize("value", [np.nan, np.inf, -0.01, 1.01, True, "bad"])
def test_noise_profile_strength_validation(value: Any) -> None:
    with pytest.raises(ValueError):
        NoiseProfile("invalid", value, 0, 0)
