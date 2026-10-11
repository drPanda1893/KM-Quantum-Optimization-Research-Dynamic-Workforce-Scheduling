"""Audit the frozen scaled-integer Ising model against Qiskit observables."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import isfinite
from typing import TypedDict

from qiskit.quantum_info import SparsePauliOp, Statevector

from km_quantum.qaoa.ordering import basis_index_to_m2_bits
from km_quantum.qubo.ising import IsingModel, bits_to_spins

OPERATOR_ATOL = 1e-9
OPERATOR_RTOL = 1e-12


@dataclass(frozen=True)
class IsingOperator:
    """The evolution observable excludes the scalar energy offset."""

    circuit_hamiltonian: SparsePauliOp
    energy_offset: float


class OperatorVerificationReport(TypedDict):
    number_qubits: int
    state_count: int
    checked_states: int
    mismatch_count: int
    max_abs_difference: float
    passed: bool
    atol: float
    rtol: float
    energy_offset: float


def _exact_float(value: Fraction, label: str) -> float:
    """Reject approximation during translation to the SDK's float coefficients."""
    try:
        converted = float(value)
    except OverflowError as error:
        raise ValueError(f"{label} cannot be represented as a finite float") from error
    if not isfinite(converted) or Fraction.from_float(converted) != value:
        raise ValueError(f"{label} cannot be represented exactly as a finite float")
    return converted


def ising_to_operator(ising: IsingModel) -> IsingOperator:
    """Translate h_i Z_i + J_ij Z_i Z_j, dividing frozen coefficients by four."""
    offset_fraction = Fraction(ising.constant4, ising.energy_denominator)
    offset = _exact_float(offset_fraction, "The energy offset")
    terms: list[tuple[str, list[int], float]] = []
    absolute_bound = abs(offset_fraction)
    for field in ising.fields:
        coefficient = Fraction(field.coefficient, ising.energy_denominator)
        terms.append(("Z", [field.index], _exact_float(coefficient, f"Field {field.index}")))
        absolute_bound += abs(coefficient)
    for coupling in ising.couplings:
        coefficient = Fraction(coupling.coefficient, ising.energy_denominator)
        label = f"Coupling ({coupling.left}, {coupling.right})"
        terms.append(("ZZ", [coupling.left, coupling.right], _exact_float(coefficient, label)))
        absolute_bound += abs(coefficient)
    # Finite individual coefficients can still overflow during energy accumulation.
    try:
        finite_bound = isfinite(float(absolute_bound))
    except OverflowError:
        finite_bound = False
    if not finite_bound:
        raise ValueError("The model's absolute energy bound exceeds finite float range")
    if terms:
        operator = SparsePauliOp.from_sparse_list(terms, num_qubits=ising.number_variables)
    else:
        # The empty string explicitly represents the scalar 0-qubit observable.
        operator = SparsePauliOp.from_list([("I" * ising.number_variables, 0.0)])
    return IsingOperator(circuit_hamiltonian=operator, energy_offset=offset)


def verify_operator(ising: IsingModel, max_qubits: int = 14) -> OperatorVerificationReport:
    """Check every computational basis state's quantum and exact M2 energy."""
    if type(max_qubits) is not int or max_qubits < 0:
        raise ValueError("max_qubits must be a non-negative integer")
    number_qubits = ising.number_variables
    if number_qubits > max_qubits:
        raise ValueError(f"Full operator verification requires at most {max_qubits} qubits")
    converted = ising_to_operator(ising)
    state_count = 2**number_qubits
    mismatch_count = 0
    max_abs_difference = Fraction(0)
    atol = Fraction.from_float(OPERATOR_ATOL)
    rtol = Fraction.from_float(OPERATOR_RTOL)
    for index in range(state_count):
        bits = basis_index_to_m2_bits(index, number_qubits)
        expected = ising.energy(bits_to_spins(bits))
        state = Statevector.from_int(index, state_count)
        expectation = complex(state.expectation_value(converted.circuit_hamiltonian))
        observed = expectation.real + converted.energy_offset
        if not isfinite(observed) or not isfinite(expectation.imag):
            raise ValueError("Qiskit returned a non-finite energy during operator verification")
        difference = abs(Fraction.from_float(observed) - expected)
        max_abs_difference = max(max_abs_difference, difference)
        if difference > atol + rtol * abs(expected) or abs(expectation.imag) > OPERATOR_ATOL:
            mismatch_count += 1
    return {
        "number_qubits": number_qubits,
        "state_count": state_count,
        "checked_states": state_count,
        "mismatch_count": mismatch_count,
        "max_abs_difference": float(max_abs_difference),
        "passed": mismatch_count == 0,
        "atol": OPERATOR_ATOL,
        "rtol": OPERATOR_RTOL,
        "energy_offset": converted.energy_offset,
    }
