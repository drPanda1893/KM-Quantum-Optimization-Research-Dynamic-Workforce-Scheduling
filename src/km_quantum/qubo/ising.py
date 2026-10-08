"""Exact, SDK-independent QUBO-to-Ising substitution, including the offset."""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from typing import Literal, Self

from pydantic import Field, model_validator

from km_quantum.domain import FrozenModel
from km_quantum.qubo.models import LinearTerm, Polynomial, QuadraticTerm, VariableMapping


def bits_to_spins(bits: Sequence[int] | str) -> tuple[int, ...]:
    """Use x=(1-z)/2: bit zero corresponds to spin +1."""
    if isinstance(bits, str):
        if any(bit not in "01" for bit in bits):
            raise ValueError("A bitstring must contain only 0 and 1")
        values = tuple(int(bit) for bit in bits)
    else:
        values = tuple(bits)
        if any(type(bit) is not int or bit not in (0, 1) for bit in values):
            raise ValueError("Bits must be integer 0 or 1")
    return tuple(1 - 2 * bit for bit in values)


def spins_to_bits(spins: Sequence[int]) -> tuple[int, ...]:
    values = tuple(spins)
    if any(type(spin) is not int or spin not in (-1, 1) for spin in values):
        raise ValueError("Spins must be integer -1 or +1")
    return tuple((1 - spin) // 2 for spin in values)


class IsingModel(FrozenModel):
    """Serialize 4H as integers; physical fields and couplings divide by four."""

    schema_version: Literal[1] = 1
    representation: Literal["ising-scaled-integer"] = "ising-scaled-integer"
    number_variables: int = Field(ge=0, strict=True)
    energy_denominator: Literal[4] = 4
    constant4: int = Field(strict=True)
    fields: tuple[LinearTerm, ...] = ()
    couplings: tuple[QuadraticTerm, ...] = ()
    mapping: VariableMapping | None = None

    @model_validator(mode="after")
    def canonical_terms(self) -> Self:
        indices = [term.index for term in self.fields]
        pairs = [(term.left, term.right) for term in self.couplings]
        if indices != sorted(set(indices)) or any(
            term.index >= self.number_variables or term.coefficient == 0 for term in self.fields
        ):
            raise ValueError("Fields must have unique sorted indices within the spin dimension")
        if pairs != sorted(set(pairs)) or any(
            not (term.left < term.right < self.number_variables) or term.coefficient == 0
            for term in self.couplings
        ):
            raise ValueError("Couplings must have unique sorted pairs within the spin dimension")
        if self.mapping is not None and len(self.mapping.variables) != self.number_variables:
            raise ValueError("Mapping size must match number_variables")
        return self

    def energy4(self, spins: Sequence[int]) -> int:
        """Evaluate four times physical energy with exact Python integers."""
        values = tuple(spins)
        spins_to_bits(values)
        if len(values) != self.number_variables:
            raise ValueError(f"Expected {self.number_variables} spins, received {len(values)}")
        return (
            self.constant4
            + sum(term.coefficient * values[term.index] for term in self.fields)
            + sum(
                term.coefficient * values[term.left] * values[term.right] for term in self.couplings
            )
        )

    def energy(self, spins: Sequence[int]) -> Fraction:
        return Fraction(self.energy4(spins), self.energy_denominator)


def qubo_to_ising(model: Polynomial) -> IsingModel:
    """Substitute x_i=(1-z_i)/2; no approximation or normalization is applied."""
    fields = {term.index: -2 * term.coefficient for term in model.linear}
    for term in model.quadratic:
        fields[term.left] = fields.get(term.left, 0) - term.coefficient
        fields[term.right] = fields.get(term.right, 0) - term.coefficient
    return IsingModel(
        number_variables=model.number_variables,
        constant4=(
            4 * model.constant
            + 2 * sum(term.coefficient for term in model.linear)
            + sum(term.coefficient for term in model.quadratic)
        ),
        fields=tuple(
            LinearTerm(index=index, coefficient=coefficient)
            for index, coefficient in sorted(fields.items())
            if coefficient
        ),
        couplings=model.quadratic,
        mapping=getattr(model, "mapping", None),
    )


def evaluate_ising_energy(model: IsingModel, spins: Sequence[int]) -> Fraction:
    return model.energy(spins)
