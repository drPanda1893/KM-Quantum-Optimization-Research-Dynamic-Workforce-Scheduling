"""Physical routing, classical permutations, and literal M2 count decoding."""

from dataclasses import replace

import numpy as np
import pytest
from qiskit import QuantumCircuit

from km_quantum.m5.circuits import (
    compile_hardware,
    compiled_ideal_probabilities,
    logical_probabilities,
)
from km_quantum.m5.mapping import (
    MeasurementMapping,
    compact_simulation_copy,
    decode_classical_counts,
    marginal_m2_probabilities,
    marginal_register_probabilities,
    raw_counts_to_m2,
)
from km_quantum.m5.snapshots import SyntheticBackend


def test_nonidentical_layout_classical_permutation_and_asymmetric_sample() -> None:
    backend = SyntheticBackend(two_qubit_gate="cx")
    circuit = QuantumCircuit(3)
    circuit.x(0)
    circuit.cx(0, 2)
    compiled = compile_hardware(
        circuit, backend.target, backend.physical_subset, m2_to_classical=(2, 0, 1)
    )
    assert compiled.mapping.logical_to_initial_physical == (1, 4, 9)
    assert compiled.mapping.m2_to_classical == (2, 0, 1)
    assert compiled.mapping.m2_to_final_physical != (0, 1, 2)
    assert raw_counts_to_m2({"110": 128}, compiled.mapping, shots=128) == {"101": 128}
    probabilities = compiled_ideal_probabilities(compiled)
    assert probabilities[5] == pytest.approx(1.0, abs=1e-12)
    np.testing.assert_allclose(probabilities, logical_probabilities(circuit), rtol=0, atol=1e-12)


def test_marginalization_keeps_nontrivial_routing_and_unused_device_wires() -> None:
    mapping = MeasurementMapping((0, 1), (9, 1), (31, 4), (1, 0), (1, 4, 9, 31), 128)
    physical = np.zeros(16)
    # Measured bits are (q31,q4)=(1,0); boundary states q1 and q9 are marginalized.
    physical[8] = 0.2
    physical[9] = 0.3
    physical[12] = 0.5
    np.testing.assert_allclose(marginal_m2_probabilities(physical, mapping), [0, 1, 0, 0])
    assert mapping.m2_to_compact == (3, 1)
    assert raw_counts_to_m2({"10": 19, "01": 3}, mapping, shots=22) == {"01": 3, "10": 19}


def test_compaction_is_only_wire_relabeling_and_preserves_provider_binary() -> None:
    backend = SyntheticBackend(num_qubits=128)
    circuit = QuantumCircuit(2)
    circuit.h(0)
    circuit.cx(0, 1)
    compiled = compile_hardware(circuit, backend.target, (1, 9, 4, 13, 22, 31, 42))
    before = compiled.provider_circuit.copy()
    initial_before = compiled.provider_circuit.layout.initial_index_layout()
    final_before = compiled.provider_circuit.layout.final_index_layout()
    compact = compact_simulation_copy(compiled.provider_circuit, compiled.mapping)
    assert compiled.provider_circuit == before
    assert compiled.provider_circuit.layout.initial_index_layout() == initial_before
    assert compiled.provider_circuit.layout.final_index_layout() == final_before
    assert len(compact.data) == len(compiled.provider_circuit.data)
    for full, small in zip(compiled.provider_circuit.data, compact.data, strict=True):
        assert full.operation == small.operation
        assert [
            compiled.mapping.physical_to_compact[compiled.provider_circuit.find_bit(q).index]
            for q in full.qubits
        ] == [compact.find_bit(q).index for q in small.qubits]
    with pytest.raises(ValueError, match="all active"):
        compact_simulation_copy(
            compiled.provider_circuit,
            replace(compiled.mapping, active_physical_qubits=compiled.mapping.m2_to_final_physical),
        )


@pytest.mark.parametrize(
    "counts,shots",
    [
        ({"00": -1}, 1),
        ({"00": True}, 1),
        ({"00": 1.0}, 1),
        ({"0": 1}, 1),
        ({"0x": 1}, 1),
        ({"0 0": 1}, 1),
        ({"00": 1}, 2),
        ({}, 1),
        ({"00": 0}, 1),
    ],
)
def test_malformed_or_incomplete_counts_block(counts: dict, shots: int) -> None:
    mapping = MeasurementMapping((0, 1), (9, 1), (9, 1), (1, 0), (1, 9), 53)
    with pytest.raises(ValueError):
        raw_counts_to_m2(counts, mapping, shots=shots)


def test_mapping_and_full_width_probability_guards() -> None:
    with pytest.raises(ValueError, match="permutation"):
        MeasurementMapping((0, 1), (1, 4), (1, 4), (0, 0), (1, 4), 53)
    mapping = MeasurementMapping((0, 1), (1, 4), (1, 4), (0, 1), (1, 4), 128)
    with pytest.raises(ValueError, match="bounded"):
        marginal_m2_probabilities(np.zeros(8), mapping)
    with pytest.raises(ValueError, match="sum"):
        marginal_m2_probabilities([0, 0, 0, 0], mapping)


def test_shared_mapping_helpers_handle_ancilla_marginalization_and_declared_wider_register() -> (
    None
):
    probabilities = np.zeros(8)
    probabilities[1] = 0.4
    probabilities[5] = 0.6
    np.testing.assert_allclose(marginal_register_probabilities(probabilities, (0, 1)), [0, 1, 0, 0])
    assert decode_classical_counts({"10110": 7}, (4, 1, 3), shots=7, classical_width=5) == {
        "110": 7
    }
    with pytest.raises(ValueError, match="distinct"):
        marginal_register_probabilities(probabilities, (1, 1))
    with pytest.raises(ValueError, match="mapping"):
        decode_classical_counts({"01": 7}, (True, 0), shots=7)
