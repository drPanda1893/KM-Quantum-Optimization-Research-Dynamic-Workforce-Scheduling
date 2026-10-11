"""Matched product-state warm-start QAOA using the unchanged positive M3 mixer sign."""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite

import numpy as np
from numpy.typing import NDArray
from qiskit import QuantumCircuit
from qiskit.circuit import Parameter, ParameterExpression

from km_quantum.qaoa.ordering import basis_index_to_m2_bits
from km_quantum.qaoa.simulation import PreparedQAOA, build_qaoa
from km_quantum.qubo.ising import IsingModel

M3_MIXER_SIGN = 1


def warm_start_angles(
    values: Sequence[float] | NDArray[np.float64], *, epsilon: float = 0.25
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    vector = np.asarray(values, dtype=np.float64)
    if vector.ndim != 1 or not np.all(np.isfinite(vector)):
        raise ValueError("Relaxation values must be a finite one-dimensional vector")
    if np.any(vector < 0) or np.any(vector > 1):
        raise ValueError("Relaxation values must lie in [0,1]")
    if isinstance(epsilon, bool) or not isfinite(epsilon) or not 0 <= epsilon <= 0.5:
        raise ValueError("epsilon must lie in [0,0.5]")
    probabilities = np.clip(vector, epsilon, 1 - epsilon)
    angles = 2 * np.arcsin(np.sqrt(probabilities))
    return probabilities, angles


def warm_initial_state(
    values: Sequence[float] | NDArray[np.float64], *, epsilon: float = 0.25
) -> QuantumCircuit:
    _, angles = warm_start_angles(values, epsilon=epsilon)
    if len(angles) > 10:
        raise ValueError("M4 warm initial states support at most ten active qubits")
    circuit = QuantumCircuit(len(angles), name="warm_initial_state")
    for index, angle in enumerate(angles):
        circuit.ry(float(angle), index)
    return circuit


def warm_mixer_circuit(
    values: Sequence[float] | NDArray[np.float64],
    beta: float | ParameterExpression,
    *,
    epsilon: float = 0.25,
) -> QuantumCircuit:
    """Chronological RY(-theta), RZ(2 beta), RY(theta) = exp(-i beta RY Z RY†)."""
    _, angles = warm_start_angles(values, epsilon=epsilon)
    if len(angles) > 10:
        raise ValueError("M4 warm mixers support at most ten active qubits")
    if not isinstance(beta, ParameterExpression) and (isinstance(beta, bool) or not isfinite(beta)):
        raise ValueError("beta must be a finite angle or a circuit ParameterExpression")
    circuit = QuantumCircuit(len(angles), name="matched_warm_mixer")
    for index, angle in enumerate(angles):
        circuit.ry(-float(angle), index)
        circuit.rz(2 * M3_MIXER_SIGN * beta, index)
        circuit.ry(float(angle), index)
    return circuit


def build_warm_qaoa(
    ising: IsingModel,
    p: int,
    values: Sequence[float] | NDArray[np.float64],
    *,
    epsilon: float = 0.25,
    max_qubits: int = 10,
) -> PreparedQAOA:
    """Reuse actual M3 angle objects; no schedule/oracle/ground-state data is accepted."""
    if type(max_qubits) is not int or not 0 <= max_qubits <= 10:
        raise ValueError("M4 warm-start supports at most ten active qubits")
    _, angles = warm_start_angles(values, epsilon=epsilon)
    if len(angles) != ising.number_variables:
        raise ValueError("The relaxation dimension differs from the Ising dimension")
    # M3 validates p and the dimension and declares the actual beta/gamma parameter objects.
    template = build_qaoa(ising, p, max_qubits=max_qubits)
    circuit = warm_initial_state(values, epsilon=epsilon)
    if not template.parameters:
        return PreparedQAOA(circuit, (), template.operator)
    by_name: dict[str, Parameter] = {parameter.name: parameter for parameter in template.parameters}
    for layer in range(p):
        gamma, beta = by_name[f"γ[{layer}]"], by_name[f"β[{layer}]"]
        # Every cost term is diagonal and commutes. Exclude only the scalar global-phase offset.
        for field in ising.fields:
            circuit.rz(2 * field.coefficient / ising.energy_denominator * gamma, field.index)
        for coupling in ising.couplings:
            circuit.rzz(
                2 * coupling.coefficient / ising.energy_denominator * gamma,
                coupling.left,
                coupling.right,
            )
        circuit.compose(warm_mixer_circuit(values, beta, epsilon=epsilon), inplace=True)
    return PreparedQAOA(circuit, tuple(circuit.parameters), template.operator)


def warm_product_probabilities(
    values: Sequence[float] | NDArray[np.float64], *, epsilon: float = 0.25, max_qubits: int = 10
) -> NDArray[np.float64]:
    probabilities, _ = warm_start_angles(values, epsilon=epsilon)
    if type(max_qubits) is not int or not 0 <= max_qubits <= 10:
        raise ValueError("M4 warm product distributions support at most ten active qubits")
    if len(probabilities) > max_qubits:
        raise ValueError("The warm product distribution exceeds max_qubits")
    result = np.empty(2 ** len(probabilities), dtype=np.float64)
    for index in range(len(result)):
        bits = basis_index_to_m2_bits(index, len(probabilities))
        result[index] = float(
            np.prod(
                [
                    probability if bit else 1 - probability
                    for bit, probability in zip(bits, probabilities, strict=True)
                ]
            )
        )
    return result


def round_relaxation(values: Sequence[float] | NDArray[np.float64]) -> tuple[int, ...]:
    """Round raw y independently; exact ties at 0.5 round to one, without repair."""
    warm_start_angles(values, epsilon=0.0)
    return tuple(int(value >= 0.5) for value in values)
