"""Explicit M2/logical/physical/classical mapping and bounded simulation copies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from qiskit import ClassicalRegister, QuantumCircuit


@dataclass(frozen=True)
class MeasurementMapping:
    m2_to_logical: tuple[int, ...]
    logical_to_initial_physical: tuple[int, ...]
    m2_to_final_physical: tuple[int, ...]
    m2_to_classical: tuple[int, ...]
    active_physical_qubits: tuple[int, ...]
    provider_width: int
    register_name: str = "m2"

    def __post_init__(self) -> None:
        n = len(self.m2_to_logical)
        if type(self.provider_width) is not int or self.provider_width <= 0:
            raise ValueError("Provider width must be a positive integer")
        if any(type(index) is not int for index in self.m2_to_logical) or (
            sorted(self.m2_to_logical) != list(range(n))
        ):
            raise ValueError("M2-to-logical indices must be a permutation")
        if len(self.logical_to_initial_physical) != n:
            raise ValueError("Initial layout must contain each logical qubit")
        if any(type(index) is not int for index in self.m2_to_classical) or (
            sorted(self.m2_to_classical) != list(range(n))
        ):
            raise ValueError("M2-to-classical indices must be a permutation")
        for label, values in (
            ("initial", self.logical_to_initial_physical),
            ("final", self.m2_to_final_physical),
            ("active", self.active_physical_qubits),
        ):
            if len(set(values)) != len(values) or any(
                type(value) is not int or not 0 <= value < self.provider_width for value in values
            ):
                raise ValueError(f"Invalid {label} physical qubit mapping")
        if len(self.m2_to_final_physical) != n:
            raise ValueError("Final mapping must contain each M2 variable")
        if not set(self.m2_to_final_physical).issubset(self.active_physical_qubits):
            raise ValueError("Every measured physical qubit must remain in the simulation copy")

    @property
    def physical_to_compact(self) -> dict[int, int]:
        return {physical: compact for compact, physical in enumerate(self.active_physical_qubits)}

    @property
    def m2_to_compact(self) -> tuple[int, ...]:
        positions = self.physical_to_compact
        return tuple(positions[physical] for physical in self.m2_to_final_physical)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "m2_to_compact": self.m2_to_compact}


def terminal_measurements(
    logical: QuantumCircuit, *, m2_to_classical: Sequence[int] | None = None
) -> QuantumCircuit:
    """Attach only the relevant logical measurements before hardware transpilation."""
    if logical.num_clbits or any(item.operation.name == "measure" for item in logical.data):
        raise ValueError("Logical reconstruction must have no pre-existing measurements")
    n = logical.num_qubits
    order = tuple(range(n)) if m2_to_classical is None else tuple(m2_to_classical)
    if sorted(order) != list(range(n)):
        raise ValueError("M2-to-classical indices must be a permutation")
    result = logical.copy()
    result.add_register(ClassicalRegister(n, "m2"))
    for variable, classical in enumerate(order):
        result.measure(variable, result.cregs[0][classical])
    return result


def active_physical_qubits(circuit: QuantumCircuit) -> tuple[int, ...]:
    """Retain every wire involved in any instruction, including routing and measurements."""
    return tuple(
        sorted({circuit.find_bit(bit).index for item in circuit.data for bit in item.qubits})
    )


def mapping_from_compiled(
    circuit: QuantumCircuit,
    initial_layout: Sequence[int],
    *,
    m2_to_classical: Sequence[int] | None = None,
) -> MeasurementMapping:
    n = len(initial_layout)
    order = tuple(range(n)) if m2_to_classical is None else tuple(m2_to_classical)
    if len(circuit.cregs) != 1 or circuit.cregs[0].name != "m2" or circuit.num_clbits != n:
        raise ValueError("Compiled circuit must have exactly the terminal m2 register")
    measured: dict[int, int] = {}
    seen_measurement = False
    for item in circuit.data:
        name = item.operation.name
        if name == "measure":
            seen_measurement = True
            classical = circuit.find_bit(item.clbits[0]).index
            if classical in measured:
                raise ValueError("Each classical bit must have one terminal measurement")
            measured[classical] = circuit.find_bit(item.qubits[0]).index
        elif seen_measurement and name != "barrier":
            raise ValueError("Measurements must be terminal")
        elif item.clbits or name == "reset":
            raise ValueError("Dynamic circuits and reset are outside M5")
    if set(measured) != set(range(n)):
        raise ValueError("Each M2 bit needs one actual terminal measurement")
    final = tuple(measured[classical] for classical in order)
    if circuit.layout is not None:
        layout_final = circuit.layout.final_index_layout(filter_ancillas=True)
        if tuple(layout_final[:n]) != final:
            raise ValueError("Actual measurement mapping disagrees with the final routing layout")
    return MeasurementMapping(
        tuple(range(n)),
        tuple(initial_layout),
        final,
        order,
        active_physical_qubits(circuit),
        circuit.num_qubits,
    )


def compact_simulation_copy(
    circuit: QuantumCircuit, mapping: MeasurementMapping, *, max_active_qubits: int = 10
) -> QuantumCircuit:
    """Pure wire relabeling: the provider circuit and its layout are untouched."""
    active = active_physical_qubits(circuit)
    if active != mapping.active_physical_qubits:
        raise ValueError("Mapping does not cover all active provider wires")
    if type(max_active_qubits) is not int or not 1 <= max_active_qubits <= 10:
        raise ValueError("M5 simulation permits at most ten active qubits")
    if len(active) > max_active_qubits:
        raise ValueError("Active routing and measurement width exceeds the simulation guard")
    if circuit.num_parameters:
        raise ValueError("A simulation copy needs fully bound parameters")
    result = QuantumCircuit(len(active), name=f"{circuit.name}_compact")
    for register in circuit.cregs:
        result.add_register(ClassicalRegister(len(register), register.name))
    result.global_phase = circuit.global_phase
    positions = mapping.physical_to_compact
    for item in circuit.data:
        result.append(
            item.operation.copy(),
            [result.qubits[positions[circuit.find_bit(bit).index]] for bit in item.qubits],
            [result.clbits[circuit.find_bit(bit).index] for bit in item.clbits],
        )
    result.metadata = {
        "artifact_type": "m5_compact_simulation_copy",
        "compact_to_physical": list(active),
        "provider_width": circuit.num_qubits,
        "provider_circuit_modified": False,
    }
    return result


def marginal_m2_probabilities(
    physical_probabilities: NDArray[np.float64] | Sequence[float],
    mapping: MeasurementMapping,
) -> NDArray[np.float64]:
    """Marginalize all routing/boundary qubits and use variable i as binary weight 2**i."""
    width = len(mapping.active_physical_qubits)
    if width > 10 or np.asarray(physical_probabilities).shape != (1 << width,):
        raise ValueError("Probability vector must cover the bounded active physical space")
    return marginal_register_probabilities(physical_probabilities, mapping.m2_to_compact)


def marginal_register_probabilities(
    physical_probabilities: NDArray[np.float64] | Sequence[float],
    measured_compact_by_m2: Sequence[int],
) -> NDArray[np.float64]:
    """The shared ideal/noisy marginalization uses exactly one M2 mapping formula."""
    values = np.asarray(physical_probabilities, dtype=np.float64)
    count = values.size
    if values.ndim != 1 or count < 1 or count > 1024 or count & (count - 1):
        raise ValueError("Probability vector must cover the bounded active physical space")
    width = count.bit_length() - 1
    measured = tuple(measured_compact_by_m2)
    if len(set(measured)) != len(measured) or any(
        type(index) is not int or not 0 <= index < width for index in measured
    ):
        raise ValueError("Measured compact indices must be distinct valid physical wires")
    if not np.all(np.isfinite(values)) or np.any(values < -1e-14):
        raise ValueError("Physical probabilities must be finite and nonnegative")
    if not np.isclose(values.sum(), 1, rtol=0, atol=1e-10):
        raise ValueError("Physical probabilities must sum to one")
    result = np.zeros(1 << len(measured), dtype=np.float64)
    for physical_index, probability in enumerate(values):
        m2_index = sum(
            ((physical_index >> compact) & 1) << variable
            for variable, compact in enumerate(measured)
        )
        result[m2_index] += max(0.0, float(probability))
    return result


def raw_counts_to_m2(
    counts: Mapping[str, int], mapping: MeasurementMapping, *, shots: int
) -> dict[str, int]:
    """Decode the actual classical register; returned keys are x[0]...x[n-1]."""
    return decode_classical_counts(counts, mapping.m2_to_classical, shots=shots)


def decode_classical_counts(
    counts: Mapping[str, int],
    m2_to_classical: Sequence[int],
    *,
    shots: int,
    classical_width: int | None = None,
) -> dict[str, int]:
    """The shared count decoder also supports explicitly declared wider diagnostic registers."""
    order = tuple(m2_to_classical)
    n = len(order) if classical_width is None else classical_width
    if (
        type(n) is not int
        or n <= 0
        or len(set(order)) != len(order)
        or any(type(index) is not int or not 0 <= index < n for index in order)
    ):
        raise ValueError("Classical mapping needs distinct valid register indices")
    if type(shots) is not int or shots <= 0 or not counts:
        raise ValueError("Counts need a positive declared shot total")
    result: dict[str, int] = {}
    for displayed, count in counts.items():
        if (
            not isinstance(displayed, str)
            or len(displayed) != n
            or any(bit not in "01" for bit in displayed)
            or type(count) is not int
            or count < 0
        ):
            raise ValueError("Raw m2 register counts must be complete nonnegative integers")
        key = "".join(displayed[n - 1 - classical] for classical in order)
        result[key] = result.get(key, 0) + count
    if sum(result.values()) != shots:
        raise ValueError("Raw counts do not sum to the declared shots")
    return dict(sorted(result.items()))
