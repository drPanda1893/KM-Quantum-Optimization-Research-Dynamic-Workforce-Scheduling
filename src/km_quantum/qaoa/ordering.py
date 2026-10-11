"""Translate Qiskit displays and basis indices to the frozen M2 variable order."""

from __future__ import annotations

from collections.abc import Sequence


def _validate_dimension(number_variables: int) -> None:
    if type(number_variables) is not int or number_variables < 0:
        raise ValueError("The number of variables must be a non-negative integer")


def _m2_bits(bits: Sequence[int] | str, number_variables: int | None = None) -> tuple[int, ...]:
    if isinstance(bits, str):
        if any(bit not in "01" for bit in bits):
            raise ValueError("An M2 bitstring must contain only 0 and 1")
        values = tuple(int(bit) for bit in bits)
    else:
        values = tuple(bits)
        if any(type(bit) is not int or bit not in (0, 1) for bit in values):
            raise ValueError("M2 bits must be integer 0 or 1")
    if number_variables is not None:
        _validate_dimension(number_variables)
        if len(values) != number_variables:
            raise ValueError(f"Expected {number_variables} bits, received {len(values)}")
    return values


def qiskit_bitstring_to_m2_bits(
    bitstring: str, number_variables: int | None = None
) -> tuple[int, ...]:
    """Qiskit prints q[n-1]...q[0]; M2 stores x[0],...,x[n-1]."""
    if not isinstance(bitstring, str):
        raise ValueError("A Qiskit bitstring must be a string")
    return _m2_bits(bitstring[::-1], number_variables)


def m2_bits_to_qiskit_bitstring(
    bits: Sequence[int] | str, number_variables: int | None = None
) -> str:
    """Render an M2 assignment using Qiskit's q[n-1]...q[0] convention."""
    return "".join(str(bit) for bit in reversed(_m2_bits(bits, number_variables)))


def m2_bits_to_basis_index(bits: Sequence[int] | str, number_variables: int | None = None) -> int:
    """Qubit/variable i has binary weight 2**i."""
    return sum(bit << index for index, bit in enumerate(_m2_bits(bits, number_variables)))


def basis_index_to_m2_bits(index: int, number_variables: int) -> tuple[int, ...]:
    """Decode a Qiskit statevector index into the M2 variable sequence."""
    _validate_dimension(number_variables)
    if type(index) is not int or not 0 <= index < 2**number_variables:
        raise ValueError(f"The basis index must be an integer in [0, {2**number_variables})")
    return tuple((index >> variable) & 1 for variable in range(number_variables))
