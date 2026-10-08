"""Exact independent substitution checks across complete tiny state spaces."""

from fractions import Fraction
from itertools import product
from random import Random

import pytest
from pydantic import ValidationError

from km_quantum.qubo.ising import (
    IsingModel,
    bits_to_spins,
    evaluate_ising_energy,
    qubo_to_ising,
    spins_to_bits,
)
from km_quantum.qubo.models import LinearTerm, Polynomial, QuadraticTerm


def test_ising_coefficients_and_offset_are_exact() -> None:
    # Q=7+3x0-5x1+9x0x1. Independent substitution gives
    # 4H=33-15z0+z1+9z0z1, including the constant.
    qubo = Polynomial(
        number_variables=2,
        constant=7,
        linear=(LinearTerm(index=0, coefficient=3), LinearTerm(index=1, coefficient=-5)),
        quadratic=(QuadraticTerm(left=0, right=1, coefficient=9),),
    )
    ising = qubo_to_ising(qubo)
    assert ising.constant4 == 33
    assert [(term.index, term.coefficient) for term in ising.fields] == [(0, -15), (1, 1)]
    assert [(term.left, term.right, term.coefficient) for term in ising.couplings] == [(0, 1, 9)]
    assert ising.energy_denominator == 4
    for bits in product((0, 1), repeat=2):
        spins = bits_to_spins(bits)
        assert qubo.energy(bits) == ising.energy(spins)
        assert evaluate_ising_energy(ising, spins) == qubo.energy(bits)
        assert spins_to_bits(spins) == bits
    assert IsingModel.model_validate_json(ising.model_dump_json()) == ising


@pytest.mark.parametrize("seed", range(24))
def test_random_integer_polynomials_preserve_every_state(seed: int) -> None:
    rng = Random(seed)
    n = seed % 6
    linear = tuple(
        LinearTerm(index=index, coefficient=value)
        for index in range(n)
        if (value := rng.randint(-99, 99))
    )
    quadratic = tuple(
        QuadraticTerm(left=left, right=right, coefficient=value)
        for left in range(n)
        for right in range(left + 1, n)
        if (value := rng.randint(-99, 99))
    )
    qubo = Polynomial(
        number_variables=n, constant=rng.randint(-99, 99), linear=linear, quadratic=quadratic
    )
    ising = qubo_to_ising(qubo)
    for bits in product((0, 1), repeat=n):
        assert ising.energy4(bits_to_spins(bits)) == 4 * qubo.energy(bits)


def test_huge_coefficients_and_fractional_spin_energies_remain_exact() -> None:
    large = 10**100 + 1
    qubo = Polynomial(
        number_variables=2,
        constant=-large,
        quadratic=(QuadraticTerm(left=0, right=1, coefficient=large),),
    )
    ising = qubo_to_ising(qubo)
    assert ising.fields[0].coefficient == -large
    assert Fraction(ising.fields[0].coefficient, 4).denominator == 4
    for bits in product((0, 1), repeat=2):
        assert ising.energy(bits_to_spins(bits)) == qubo.energy(bits)
    assert IsingModel.model_validate_json(ising.model_dump_json()) == ising


@pytest.mark.parametrize("values", [(0,), (2,), (True,), (1.0,), (-1, 1, 0)])
def test_spin_values_are_strict(values: tuple[object, ...]) -> None:
    with pytest.raises(ValueError, match="Spins"):
        spins_to_bits(values)  # type: ignore[arg-type]


@pytest.mark.parametrize("values", [(2,), (True,), (0.0,), "02", "abc"])
def test_bit_values_are_strict(values: object) -> None:
    with pytest.raises(ValueError):
        bits_to_spins(values)  # type: ignore[arg-type]


def test_spin_dimension_is_checked_and_empty_models_are_supported() -> None:
    ising = qubo_to_ising(Polynomial(number_variables=0, constant=11))
    assert ising.energy(()) == 11
    with pytest.raises(ValueError, match="Expected 0"):
        ising.energy((1,))


@pytest.mark.parametrize(
    "couplings",
    [
        (QuadraticTerm(left=0, right=0, coefficient=1),),
        (QuadraticTerm(left=1, right=0, coefficient=1),),
        (QuadraticTerm(left=0, right=2, coefficient=1),),
        (QuadraticTerm(left=0, right=1, coefficient=0),),
    ],
)
def test_ising_couplings_require_canonical_nonzero_pairs(
    couplings: tuple[QuadraticTerm, ...],
) -> None:
    with pytest.raises(ValidationError):
        IsingModel(number_variables=2, constant4=0, couplings=couplings)
