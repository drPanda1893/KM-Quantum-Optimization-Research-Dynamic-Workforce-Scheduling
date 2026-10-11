"""Independent matrices and amplitudes for the matched positive-sign warm mixer."""

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator, Statevector
from scipy.linalg import expm

from km_quantum.m4.warm_start import (
    M3_MIXER_SIGN,
    build_warm_qaoa,
    round_relaxation,
    warm_initial_state,
    warm_mixer_circuit,
    warm_product_probabilities,
    warm_start_angles,
)
from km_quantum.qaoa.simulation import ExactEvaluator, bind_parameters, build_qaoa
from km_quantum.qubo.ising import IsingModel
from km_quantum.qubo.models import LinearTerm, QuadraticTerm


def _ry(theta: float) -> np.ndarray:
    return np.array(
        [[np.cos(theta / 2), -np.sin(theta / 2)], [np.sin(theta / 2), np.cos(theta / 2)]]
    )


@pytest.mark.parametrize("raw", [0.0, 0.23, 0.51, 0.83, 1.0])
def test_initial_one_probability_is_the_clipped_relaxation_value(raw: float) -> None:
    probabilities, angles = warm_start_angles([raw])
    state = Statevector.from_instruction(warm_initial_state([raw]))
    assert state.probabilities()[1] == pytest.approx(probabilities[0], abs=1e-14)
    assert probabilities[0] == np.clip(raw, 0.25, 0.75)
    np.testing.assert_allclose(state.data, _ry(float(angles[0])) @ np.array([1, 0]), atol=1e-14)


@pytest.mark.parametrize("raw", [0.05, 0.33, 0.78, 0.95])
@pytest.mark.parametrize("beta", [-0.47, 0.0, 0.61])
def test_mixer_matches_independent_hamiltonian_matrix_exponential(raw: float, beta: float) -> None:
    c = float(np.clip(raw, 0.25, 0.75))
    theta = 2 * np.arcsin(np.sqrt(c))
    rotation = _ry(float(theta))
    hamiltonian = np.array(
        [[1 - 2 * c, 2 * np.sqrt(c * (1 - c))], [2 * np.sqrt(c * (1 - c)), 2 * c - 1]]
    )
    np.testing.assert_allclose(hamiltonian, rotation @ np.diag([1, -1]) @ rotation.T, atol=1e-14)
    expected = expm(-1j * beta * hamiltonian)
    actual = Operator(warm_mixer_circuit([raw], beta)).data
    np.testing.assert_allclose(actual, expected, atol=1e-14, rtol=1e-14)
    assert M3_MIXER_SIGN == 1
    # The initial state is its +1 eigenstate under the actual M3 sign.
    start = rotation @ np.array([1, 0])
    np.testing.assert_allclose(hamiltonian @ start, start, atol=1e-14)
    np.testing.assert_allclose(actual @ start, np.exp(-1j * beta) * start, atol=1e-14)


@pytest.mark.parametrize("beta", [-0.3, 0.0, 0.87])
def test_epsilon_half_reproduces_m3_x_mixer_and_plus_state(beta: float) -> None:
    mixer = Operator(warm_mixer_circuit([0.03], beta, epsilon=0.5)).data
    expected = expm(-1j * beta * np.array([[0, 1], [1, 0]]))
    np.testing.assert_allclose(mixer, expected, atol=1e-14)
    state = Statevector.from_instruction(warm_initial_state([0.03], epsilon=0.5))
    np.testing.assert_allclose(state.data, [2**-0.5] * 2, atol=1e-14)


@pytest.mark.parametrize("p", [1, 2])
@pytest.mark.parametrize("size", [1, 2, 3])
def test_complete_epsilon_half_ansatz_matches_untouched_m3(p: int, size: int) -> None:
    fields = tuple(LinearTerm(index=i, coefficient=(3, -5, 2)[i]) for i in range(size))
    couplings = tuple(
        QuadraticTerm(left=i, right=i + 1, coefficient=(7, -3)[i]) for i in range(size - 1)
    )
    ising = IsingModel(number_variables=size, constant4=17, fields=fields, couplings=couplings)
    standard = build_qaoa(ising, p)
    warm = build_warm_qaoa(ising, p, [0.13, 0.81, 0.41][:size], epsilon=0.5)
    assert [parameter.name for parameter in warm.parameters] == [
        parameter.name for parameter in standard.parameters
    ]
    values = np.linspace(0.13, 0.91, len(standard.parameters))
    expected = Statevector.from_instruction(bind_parameters(standard, values))
    actual = Statevector.from_instruction(bind_parameters(warm, values))
    assert actual.equiv(expected, rtol=1e-12, atol=1e-12)
    assert ExactEvaluator(warm).evaluate(values) == pytest.approx(
        ExactEvaluator(standard).evaluate(values), abs=1e-10
    )
    assert warm.operator.energy_offset == standard.operator.energy_offset == 4.25


def test_asymmetric_two_qubit_cost_and_warm_mixer_have_independent_full_amplitudes() -> None:
    ising = IsingModel(
        number_variables=2,
        constant4=19,
        fields=(LinearTerm(index=0, coefficient=3), LinearTerm(index=1, coefficient=-5)),
        couplings=(QuadraticTerm(left=0, right=1, coefficient=7),),
    )
    raw, beta, gamma = [0.29, 0.68], 0.41, 0.83
    warm = build_warm_qaoa(ising, 1, raw)
    by_name = {"β[0]": beta, "γ[0]": gamma}
    bound = bind_parameters(warm, [by_name[parameter.name] for parameter in warm.parameters])
    # Qubit zero is the low-order/right Kronecker factor, and fields are asymmetric.
    rotations = [_ry(float(2 * np.arcsin(np.sqrt(c)))) for c in raw]
    state = np.kron(rotations[1] @ [1, 0], rotations[0] @ [1, 0]).astype(complex)
    cost = np.array(
        [0.75 - 1.25 + 1.75, -0.75 - 1.25 - 1.75, 0.75 + 1.25 - 1.75, -0.75 + 1.25 + 1.75]
    )
    state *= np.exp(-1j * gamma * cost)
    mixers = [expm(-1j * beta * rotation @ np.diag([1, -1]) @ rotation.T) for rotation in rotations]
    expected = np.kron(mixers[1], mixers[0]) @ state
    np.testing.assert_allclose(Statevector.from_instruction(bound).data, expected, atol=1e-13)
    assert ExactEvaluator(warm).evaluate([beta, gamma]) == pytest.approx(
        float(np.dot(np.abs(expected) ** 2, cost)) + 4.75, abs=1e-10
    )


def test_product_distribution_uses_m2_indices_and_matches_independent_bernoulli_bits() -> None:
    raw = [0.25, 0.71, 0.34]
    expected = np.array(
        [
            np.prod([raw[i] if index & (1 << i) else 1 - raw[i] for i in range(3)])
            for index in range(8)
        ]
    )
    actual = warm_product_probabilities(raw)
    np.testing.assert_allclose(actual, expected, atol=1e-14)
    np.testing.assert_allclose(
        actual, Statevector.from_instruction(warm_initial_state(raw)).probabilities(), atol=1e-14
    )
    assert sum(actual) == pytest.approx(1)
    assert warm_product_probabilities([]).tolist() == [1]
    assert round_relaxation([0, 0.499, 0.5, 0.9, 1]) == (0, 0, 1, 1, 1)


def test_multiqubit_mixer_preserves_its_entire_product_state_up_to_global_phase() -> None:
    raw, beta = [0.2, 0.7, 0.41], 0.82
    initial = warm_initial_state(raw)
    start = Statevector.from_instruction(initial)
    final = Statevector.from_instruction(initial.compose(warm_mixer_circuit(raw, beta)))
    np.testing.assert_allclose(final.data, np.exp(-3j * beta) * start.data, atol=1e-13)


@pytest.mark.parametrize("size", [0, 2])
def test_constant_cost_has_no_useless_search_parameters(size: int) -> None:
    prepared = build_warm_qaoa(IsingModel(number_variables=size, constant4=17), 2, [0.7] * size)
    assert prepared.parameters == ()
    assert ExactEvaluator(prepared).evaluate([]) == 4.25
    np.testing.assert_allclose(
        Statevector.from_instruction(prepared.circuit).probabilities(),
        warm_product_probabilities([0.7] * size),
        atol=1e-14,
    )


@pytest.mark.parametrize("values", [[float("nan")], [-0.1], [1.1], [[0.2]]])
def test_invalid_relaxation_vectors_are_rejected(values: list) -> None:
    with pytest.raises(ValueError):
        warm_start_angles(values)


@pytest.mark.parametrize("epsilon", [-0.01, 0.51, float("nan"), True])
def test_invalid_clipping_is_rejected(epsilon: float) -> None:
    with pytest.raises(ValueError, match="epsilon"):
        warm_start_angles([0.3], epsilon=epsilon)


def test_dimension_and_resource_limits_are_checked() -> None:
    with pytest.raises(ValueError, match="dimension"):
        build_warm_qaoa(IsingModel(number_variables=2, constant4=0), 1, [0.2])
    with pytest.raises(ValueError, match="max_qubits"):
        build_warm_qaoa(IsingModel(number_variables=11, constant4=0), 1, [0.2] * 11)
    with pytest.raises(ValueError, match="at most ten"):
        build_warm_qaoa(IsingModel(number_variables=1, constant4=0), 1, [0.2], max_qubits=11)
    with pytest.raises(ValueError, match="max_qubits"):
        warm_product_probabilities([0.2] * 11)
    with pytest.raises(ValueError, match="p=1,2,3"):
        build_warm_qaoa(IsingModel(number_variables=1, constant4=0), 0, [0.2])
    with pytest.raises(ValueError, match="at most ten"):
        warm_initial_state([0.2] * 11)
    with pytest.raises(ValueError, match="at most ten"):
        warm_mixer_circuit([0.2] * 11, 0.3)
    with pytest.raises(ValueError, match="finite angle"):
        warm_mixer_circuit([0.2], float("nan"))


def test_mixer_gate_order_is_chronological_negative_theta_then_rz_then_positive_theta() -> None:
    circuit = warm_mixer_circuit([0.25], 0.31)
    assert [operation.operation.name for operation in circuit.data] == ["ry", "rz", "ry"]
    theta = 2 * np.arcsin(0.5)
    assert [float(operation.operation.params[0]) for operation in circuit.data] == pytest.approx(
        [-theta, 0.62, theta]
    )
    standard = QuantumCircuit(1)
    standard.rx(0.62, 0)
    assert Operator(warm_mixer_circuit([0.25], 0.31, epsilon=0.5)).equiv(Operator(standard))
