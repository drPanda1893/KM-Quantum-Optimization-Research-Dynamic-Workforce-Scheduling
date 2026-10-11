"""Prerequisite operator and bit-order gate before any QAOA execution."""

import json
from fractions import Fraction
from pathlib import Path

import pytest
from qiskit.quantum_info import SparsePauliOp, Statevector

from km_quantum.qaoa import operators
from km_quantum.qaoa.operators import IsingOperator, ising_to_operator, verify_operator
from km_quantum.qaoa.ordering import (
    basis_index_to_m2_bits,
    m2_bits_to_basis_index,
    m2_bits_to_qiskit_bitstring,
    qiskit_bitstring_to_m2_bits,
)
from km_quantum.qubo.ising import IsingModel, bits_to_spins
from km_quantum.qubo.models import LinearTerm, QuadraticTerm

ROOT = Path(__file__).resolve().parents[1]
FROZEN = [
    record
    for line in (ROOT / "data" / "synthetic" / "m2" / "frozen_qubos.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if (record := json.loads(line))["ising_model"]["number_variables"] <= 14
]


def test_single_z_has_correct_sign_and_separate_quarter_offset() -> None:
    ising = IsingModel(
        number_variables=1, constant4=3, fields=(LinearTerm(index=0, coefficient=5),)
    )
    converted = ising_to_operator(ising)
    assert converted.energy_offset == 0.75
    assert converted.circuit_hamiltonian.to_list() == [("Z", 1.25 + 0j)]
    expected = (2.0, -0.5)
    for index, energy in enumerate(expected):
        state = Statevector.from_int(index, 2)
        assert state.expectation_value(converted.circuit_hamiltonian).real == energy - 0.75
    assert verify_operator(ising)["passed"]


def test_asymmetric_fields_and_zz_are_applied_to_the_m2_indices() -> None:
    # 4H = 7 + z0 - 6z1 + 11z2 + 3z0z2 - 9z1z2.
    ising = IsingModel(
        number_variables=3,
        constant4=7,
        fields=(
            LinearTerm(index=0, coefficient=1),
            LinearTerm(index=1, coefficient=-6),
            LinearTerm(index=2, coefficient=11),
        ),
        couplings=(
            QuadraticTerm(left=0, right=2, coefficient=3),
            QuadraticTerm(left=1, right=2, coefficient=-9),
        ),
    )
    converted = ising_to_operator(ising)
    assert converted.circuit_hamiltonian.to_list() == [
        ("IIZ", 0.25 + 0j),
        ("IZI", -1.5 + 0j),
        ("ZII", 2.75 + 0j),
        ("ZIZ", 0.75 + 0j),
        ("ZZI", -2.25 + 0j),
    ]
    for index in range(8):
        bits = basis_index_to_m2_bits(index, 3)
        spins = bits_to_spins(bits)
        manual = Fraction(
            7
            + spins[0]
            - 6 * spins[1]
            + 11 * spins[2]
            + 3 * spins[0] * spins[2]
            - 9 * spins[1] * spins[2],
            4,
        )
        state = Statevector.from_int(index, 8)
        assert state.expectation_value(converted.circuit_hamiltonian).real + 1.75 == manual
    report = verify_operator(ising)
    assert report["checked_states"] == report["state_count"] == 8
    assert report["mismatch_count"] == 0
    assert report["max_abs_difference"] == 0
    assert report["passed"]


@pytest.mark.parametrize("number_variables", [0, 1, 3])
def test_constant_models_preserve_dimension_and_exact_offset(number_variables: int) -> None:
    ising = IsingModel(number_variables=number_variables, constant4=-13)
    converted = ising_to_operator(ising)
    assert converted.circuit_hamiltonian.num_qubits == number_variables
    assert converted.energy_offset == -3.25
    report = verify_operator(ising)
    assert report["passed"]
    assert report["checked_states"] == 2**number_variables
    json.dumps(report, allow_nan=False)


def test_unrepresentable_coefficients_and_overflow_bound_are_rejected() -> None:
    with pytest.raises(ValueError, match="exactly"):
        ising_to_operator(IsingModel(number_variables=0, constant4=10**100 + 1))
    with pytest.raises(ValueError, match="exactly"):
        ising_to_operator(
            IsingModel(
                number_variables=1,
                constant4=0,
                fields=(LinearTerm(index=0, coefficient=2**54 + 1),),
            )
        )
    with pytest.raises(ValueError, match="finite float"):
        ising_to_operator(IsingModel(number_variables=0, constant4=2**2000))
    with pytest.raises(ValueError, match="absolute energy bound"):
        ising_to_operator(
            IsingModel(
                number_variables=2,
                constant4=2**1025,
                fields=(LinearTerm(index=0, coefficient=2**1025),),
            )
        )


def test_operator_gate_detects_wrong_endianness_and_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    ising = IsingModel(
        number_variables=2, constant4=3, fields=(LinearTerm(index=0, coefficient=4),)
    )
    for faulty in (
        IsingOperator(SparsePauliOp.from_list([("ZI", 1.0)]), 0.75),
        IsingOperator(SparsePauliOp.from_list([("IZ", 1.0)]), 0.0),
    ):
        monkeypatch.setattr(operators, "ising_to_operator", lambda _, value=faulty: value)
        report = verify_operator(ising)
        assert not report["passed"]
        assert report["mismatch_count"] > 0
        assert report["checked_states"] == report["state_count"] == 4


def test_ordering_round_trips_every_small_basis_index() -> None:
    for dimension in range(6):
        for index in range(2**dimension):
            bits = basis_index_to_m2_bits(index, dimension)
            qiskit_bits = m2_bits_to_qiskit_bitstring(bits, dimension)
            assert m2_bits_to_basis_index(bits, dimension) == index
            assert qiskit_bitstring_to_m2_bits(qiskit_bits, dimension) == bits
            assert m2_bits_to_basis_index("".join(map(str, bits))) == index
            if dimension:
                assert int(qiskit_bits, 2) == index
    assert qiskit_bitstring_to_m2_bits("100", 3) == (0, 0, 1)
    assert m2_bits_to_qiskit_bitstring("100", 3) == "001"


@pytest.mark.parametrize("bitstring", ["1 0", "02", "0x1", "-1", "abc", 10])
def test_invalid_qiskit_bitstrings_are_rejected(bitstring: object) -> None:
    with pytest.raises(ValueError):
        qiskit_bitstring_to_m2_bits(bitstring)  # type: ignore[arg-type]


@pytest.mark.parametrize("bits", [(True,), (0.0,), (2,), "02"])
def test_invalid_m2_bits_are_rejected(bits: object) -> None:
    with pytest.raises(ValueError):
        m2_bits_to_basis_index(bits)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        m2_bits_to_qiskit_bitstring(bits)  # type: ignore[arg-type]


def test_dimensions_and_indices_are_strict() -> None:
    for dimension in (-1, True, 1.0):
        with pytest.raises(ValueError):
            basis_index_to_m2_bits(0, dimension)  # type: ignore[arg-type]
    for index in (-1, 8, True, 0.0):
        with pytest.raises(ValueError):
            basis_index_to_m2_bits(index, 3)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Expected 3"):
        qiskit_bitstring_to_m2_bits("01", 3)
    with pytest.raises(ValueError, match="Expected 3"):
        m2_bits_to_qiskit_bitstring("01", 3)
    with pytest.raises(ValueError, match="at most 14"):
        verify_operator(IsingModel(number_variables=15, constant4=0))


@pytest.mark.parametrize("record", FROZEN, ids=[record["name"] for record in FROZEN])
def test_every_frozen_small_ising_operator_matches_all_basis_states(record: dict) -> None:
    # Parse the frozen M2 Ising JSON directly; this test must never re-encode M2.
    ising = IsingModel.model_validate(record["ising_model"])
    report = verify_operator(ising)
    assert report["passed"], (record["name"], report)
    assert report["checked_states"] == report["state_count"] == 2**ising.number_variables
    assert report["mismatch_count"] == 0
