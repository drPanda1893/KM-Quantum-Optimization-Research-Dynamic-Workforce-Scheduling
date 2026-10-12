"""Frozen M4 reconstruction, native ISA equivalence, and QPY behavior gates."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit

from km_quantum.m4 import relaxation, training
from km_quantum.m5.circuits import (
    CASES,
    FrozenCircuit,
    compile_hardware,
    compiled_ideal_probabilities,
    logical_probabilities,
    reconstruct_suite,
    smoke_circuits,
    validate_native_instructions,
    verify_compilation,
)
from km_quantum.m5.mapping import compact_simulation_copy, mapping_from_compiled
from km_quantum.m5.snapshots import (
    SyntheticBackend,
    load_qpy_bytes,
    qpy_bytes,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def suite() -> tuple[FrozenCircuit, ...]:
    return reconstruct_suite(ROOT)


def test_exact_predeclared_suite_uses_frozen_seed_zero_without_training(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("M5 must never train or resolve a relaxation")

    monkeypatch.setattr(training, "train_variant", forbidden)
    monkeypatch.setattr(training, "minimize", forbidden)
    monkeypatch.setattr(relaxation, "minimize", forbidden)
    restored = reconstruct_suite(ROOT)
    qaoa = [item for item in restored if item.p is not None]
    controls = [item for item in restored if item.p is None]
    assert len(qaoa) == 16 and len(controls) == 8
    assert {(item.reference_name, item.algorithm, item.p, item.seed) for item in qaoa} == {
        (name, algorithm, p, 0)
        for name in CASES
        for algorithm in ("standard", "warm_start")
        for p in (1, 2)
    }
    assert {(item.reference_name, item.algorithm) for item in controls} == {
        (name, algorithm) for name in CASES for algorithm in ("uniform", "warm_product")
    }
    for item in restored:
        np.testing.assert_allclose(
            logical_probabilities(item.circuit), item.ideal_probabilities, rtol=0, atol=1e-10
        )
        assert not item.circuit.num_clbits and item.epsilon == 0.25
        assert len(item.source_hashes["preparation"]["sha256"]) == 64
        assert len(item.source_hashes["preparation"]["lf_sha256"]) == 64


@pytest.mark.parametrize("position", range(24))
def test_all_main_circuits_have_native_ideal_parity_and_qpy_behavior(
    suite: tuple[FrozenCircuit, ...],
    position: int,
) -> None:
    frozen = suite[position]
    backend = SyntheticBackend()
    compiled = compile_hardware(frozen.circuit, backend.target, backend.physical_subset)
    gate = verify_compilation(frozen, compiled)
    assert gate["passed"]
    assert compiled.provider_circuit.num_qubits == 53
    assert compiled.compact_circuit.num_qubits <= 7
    assert (
        compiled.mapping.logical_to_initial_physical
        == backend.physical_subset[: frozen.circuit.num_qubits]
    )
    assert compiled.provider_circuit.num_clbits == frozen.circuit.num_qubits
    assert set(compiled.provider_circuit.count_ops()) <= {
        "rz",
        "sx",
        "x",
        "ecr",
        "measure",
        "barrier",
    }
    assert set(gate["metric_absolute_differences"]) == {
        "p_schedule_optimal",
        "p_ground_encoding",
        "p_schedule_feasible",
        "p_encoding_feasible",
    }
    loaded_logical = load_qpy_bytes(qpy_bytes(frozen.circuit))
    np.testing.assert_allclose(
        logical_probabilities(loaded_logical), frozen.ideal_probabilities, rtol=0, atol=1e-10
    )
    loaded_native = load_qpy_bytes(qpy_bytes(compiled.provider_circuit))
    assert (
        loaded_native.layout.final_index_layout()
        == compiled.provider_circuit.layout.final_index_layout()
    )
    loaded_mapping = mapping_from_compiled(
        loaded_native, compiled.mapping.logical_to_initial_physical
    )
    assert loaded_mapping == compiled.mapping
    loaded_compact = compact_simulation_copy(loaded_native, loaded_mapping)
    restored = replace(
        compiled,
        provider_circuit=loaded_native,
        compact_circuit=loaded_compact,
        mapping=loaded_mapping,
    )
    assert verify_compilation(frozen, restored)["passed"]


@pytest.mark.parametrize("gate", ["ecr", "cz", "cx"])
def test_target_actual_two_qubit_basis_and_directed_edges_are_respected(gate: str) -> None:
    backend = SyntheticBackend(two_qubit_gate=gate)
    logical = QuantumCircuit(3)
    logical.x(0)
    logical.h(1)
    logical.cx(0, 2)  # Requires routing on the chain.
    logical.cx(1, 0)  # Logical direction opposes the native chain.
    compiled = compile_hardware(logical, backend.target, backend.physical_subset)
    validate_native_instructions(compiled.provider_circuit, backend.target)
    assert gate in compiled.provider_circuit.count_ops()
    assert "swap" not in compiled.provider_circuit.count_ops()
    np.testing.assert_allclose(
        compiled_ideal_probabilities(compiled), logical_probabilities(logical), rtol=0, atol=1e-10
    )
    forbidden = QuantumCircuit(53)
    forbidden.cx(4, 1)
    with pytest.raises(ValueError, match="unsupported"):
        validate_native_instructions(forbidden, backend.target)


def test_provider_128_idle_wires_do_not_expand_simulation_and_routing_ancilla_is_retained() -> None:
    backend = SyntheticBackend(num_qubits=128)
    logical = QuantumCircuit(2)
    logical.x(0)
    logical.h(1)
    logical.cx(0, 1)
    # The first two logical IDs now have an intervening physical routing wire 4.
    subset = (1, 9, 4, 13, 22, 31, 42)
    compiled = compile_hardware(logical, backend.target, subset)
    assert compiled.provider_circuit.num_qubits == 128
    assert compiled.provider_circuit.num_clbits == 2
    assert compiled.compact_circuit.num_qubits == 3
    assert compiled.active_physical_qubits == (1, 4, 9)
    np.testing.assert_allclose(
        compiled_ideal_probabilities(compiled), logical_probabilities(logical), rtol=0, atol=1e-10
    )
    with pytest.raises(ValueError, match="guard"):
        compact_simulation_copy(compiled.provider_circuit, compiled.mapping, max_active_qubits=2)


def test_four_frozen_smoke_patterns_are_distinct_asymmetric_and_exact() -> None:
    backend = SyntheticBackend()
    patterns = []
    for logical in smoke_circuits():
        pattern = logical.metadata["expected_m2_bits"]
        patterns.append(pattern)
        compiled = compile_hardware(logical, backend.target, backend.physical_subset)
        expected = sum(int(bit) << index for index, bit in enumerate(pattern))
        probabilities = compiled_ideal_probabilities(compiled)
        assert probabilities[expected] == pytest.approx(1.0, abs=1e-12)
        assert np.argmax(probabilities) == expected
    assert patterns == ["0000000", "1111111", "1001010", "0110001"]
    assert patterns[2] != patterns[2][::-1] and patterns[3] != patterns[3][::-1]


def test_changed_distribution_and_compiler_policy_block(suite: tuple[FrozenCircuit, ...]) -> None:
    frozen = suite[0]
    backend = SyntheticBackend()
    compiled = compile_hardware(frozen.circuit, backend.target, backend.physical_subset)
    bad = np.roll(frozen.ideal_probabilities, 1)
    with pytest.raises(ValueError, match="distribution differs"):
        verify_compilation(replace(frozen, ideal_probabilities=bad), compiled)
    for kwargs in ({"optimization_level": 2}, {"seed_transpiler": 17}):
        with pytest.raises(ValueError, match="policy"):
            compile_hardware(frozen.circuit, backend.target, backend.physical_subset, **kwargs)
