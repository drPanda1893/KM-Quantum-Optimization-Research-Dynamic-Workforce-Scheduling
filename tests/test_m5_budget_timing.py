"""Pure archived timing diagnostics: no SDK service, circuit run or network."""

import copy
import json
from typing import Any

import pytest
from qiskit import ClassicalRegister, QuantumCircuit
from qiskit.circuit import Parameter

from km_quantum.m5.budget_timing import timing_components


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def deny(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Local budget timing must never access a provider")

    monkeypatch.setattr(requests.sessions.Session, "request", deny)


def snapshot() -> dict[str, Any]:
    return {
        "backend_width": 2,
        "content_sha256": "a" * 64,
        "instructions": [
            {"name": name, "qubits": list(qubits), "duration_seconds": seconds}
            for name, qubits, seconds in (
                ("x", (0,), 10e-9),
                ("sx", (1,), 1e-9),
                ("cz", (0, 1), 5e-9),
                ("measure", (0,), 1e-9),
                ("measure", (1,), 20e-9),
                ("reset", (0,), 3e-9),
                ("reset", (1,), 7e-9),
            )
        ],
    }


def circuit() -> QuantumCircuit:
    value = QuantumCircuit(2)
    value.add_register(ClassicalRegister(2, "m2"))
    value.x(0)
    value.sx(1)
    value.measure([0, 1], value.cregs[0])
    return value


def test_heterogeneous_readout_switches_the_critical_wire_instead_of_summing() -> None:
    result = timing_components(circuit(), snapshot(), archived_circuit_seconds=21e-9)
    assert result["status"] == "COMPUTED"
    assert result["full_critical_seconds"] == pytest.approx(21e-9, abs=1e-20)
    assert result["native_without_readout_seconds"] == pytest.approx(10e-9, abs=1e-20)
    assert result["readout_critical_increment_seconds"] == pytest.approx(11e-9, abs=1e-20)
    assert result["initialization_seconds"] == pytest.approx(7e-9, abs=1e-20)
    assert result["measurement_count"] == 2 and result["active_physical_qubits"] == [0, 1]
    assert result["archived_duration_comparison"]["status"] == "MATCH"
    assert result["archived_duration_comparison"]["atol_seconds"] == 1e-15
    assert not result["full_live_target_claimed"]
    assert not result["provider_contacted"] and not result["hardware_used"]


def test_swapping_readout_durations_changes_the_critical_increment() -> None:
    data = snapshot()
    for row in data["instructions"]:
        if row["name"] == "measure":
            row["duration_seconds"] = 20e-9 if row["qubits"] == [0] else 1e-9
    result = timing_components(circuit(), data)
    assert result["full_critical_seconds"] == pytest.approx(30e-9, abs=1e-20)
    assert result["readout_critical_increment_seconds"] == pytest.approx(20e-9, abs=1e-20)


def test_two_qubit_dependency_and_barrier_synchronize_parallel_native_paths() -> None:
    value = QuantumCircuit(2)
    value.add_register(ClassicalRegister(2, "m2"))
    value.x(0)
    value.sx(1)
    value.barrier()
    value.cz(0, 1)
    value.measure([0, 1], value.cregs[0])
    result = timing_components(value, snapshot())
    assert result["native_without_readout_seconds"] == pytest.approx(15e-9, abs=1e-20)
    assert result["full_critical_seconds"] == pytest.approx(35e-9, abs=1e-20)
    assert result["readout_critical_increment_seconds"] == pytest.approx(20e-9, abs=1e-20)


def test_explicit_reset_remains_native_work_in_addition_to_parallel_initialization() -> None:
    value = QuantumCircuit(2)
    value.add_register(ClassicalRegister(1, "m2"))
    value.x(0)
    value.reset(0)
    value.measure(0, 0)
    result = timing_components(value, snapshot())
    assert result["full_critical_seconds"] == pytest.approx(14e-9, abs=1e-20)
    assert result["native_without_readout_seconds"] == pytest.approx(13e-9, abs=1e-20)
    assert result["initialization_seconds"] == pytest.approx(3e-9, abs=1e-20)
    assert result["explicit_reset_count"] == 1
    assert result["active_physical_qubits"] == [0]


@pytest.mark.parametrize("invalid", [None, -1e-9, True, float("nan"), float("inf"), "1ns"])
def test_missing_negative_nonfinite_or_nonsi_duration_blocks(invalid: Any) -> None:
    data = snapshot()
    data["instructions"][0]["duration_seconds"] = invalid
    result = timing_components(circuit(), data)
    assert result["status"] == "BLOCKED"
    assert result["full_critical_seconds"] is None
    assert any("invalid SI duration" in item for item in result["missing"])
    json.dumps(result, allow_nan=False)


def test_missing_reset_does_not_invent_zero_initialization() -> None:
    data = snapshot()
    data["instructions"] = [
        row for row in data["instructions"] if not (row["name"] == "reset" and row["qubits"] == [1])
    ]
    result = timing_components(circuit(), data)
    assert result["status"] == "BLOCKED" and result["initialization_seconds"] is None


def test_missing_readout_cannot_be_masked_by_native_only_timing() -> None:
    data = snapshot()
    data["instructions"] = [row for row in data["instructions"] if row["name"] != "measure"]
    result = timing_components(circuit(), data)
    assert result["status"] == "BLOCKED"
    assert result["full_critical_seconds"] is None
    assert result["readout_critical_increment_seconds"] is None


@pytest.mark.parametrize("kind", ["delay", "dynamic"])
def test_native_delay_or_dynamic_control_is_unsupported_even_with_catalog_duration(
    kind: str,
) -> None:
    value = circuit()
    data = snapshot()
    if kind == "delay":
        value.delay(100, 0, unit="dt")
        data["instructions"].append({"name": "delay", "qubits": [0], "duration_seconds": 0})
    else:
        with value.if_test((value.clbits[0], True)):
            value.x(0)
        data["instructions"].append({"name": "if_else", "qubits": [0], "duration_seconds": 0})
    result = timing_components(value, data)
    assert result["status"] == "BLOCKED" and result["full_critical_seconds"] is None
    assert any("Unsupported static" in item for item in result["missing"])


def test_duplicate_used_duration_is_ambiguous() -> None:
    data = snapshot()
    data["instructions"].append(copy.deepcopy(data["instructions"][0]))
    assert timing_components(circuit(), data)["status"] == "BLOCKED"


def test_duration_units_are_seconds_without_dt_rescaling_or_full_target_claim() -> None:
    data = snapshot()
    data["dt"] = 1e-9
    first = timing_components(circuit(), data)
    data["dt"] = 2e-9
    second = timing_components(circuit(), data)
    assert first["full_critical_seconds"] == second["full_critical_seconds"]
    assert first["units"] == "seconds" and second["full_live_target_claimed"] is False


@pytest.mark.parametrize("difference,expected", [(1e-16, "MATCH"), (1e-13, "MISMATCH")])
def test_archived_comparison_uses_fixed_absolute_1e_minus15_tolerance(
    difference: float, expected: str
) -> None:
    result = timing_components(circuit(), snapshot(), archived_circuit_seconds=21e-9 + difference)
    assert result["archived_duration_comparison"]["status"] == expected
    assert result["status"] == ("COMPUTED" if expected == "MATCH" else "BLOCKED")


def test_zero_duration_and_no_readout_are_explicit_not_a_missing_fallback() -> None:
    value = QuantumCircuit(2)
    value.rz(0.2, 0)
    data = snapshot()
    data["instructions"].append({"name": "rz", "qubits": [0], "duration_seconds": 0.0})
    result = timing_components(value, data)
    assert result["status"] == "COMPUTED" and result["full_critical_seconds"] == 0
    assert result["readout_critical_increment_seconds"] == 0
    assert result["initialization_seconds"] == 3e-9


def test_analysis_does_not_mutate_actual_circuit_metadata_layout_or_snapshot() -> None:
    from qiskit.transpiler import Layout, TranspileLayout

    value, data = circuit(), snapshot()
    value.metadata = {"provider_pub": "frozen", "nested": {"value": 1}}
    value._layout = TranspileLayout(
        initial_layout=Layout({qubit: index for index, qubit in enumerate(value.qubits)}),
        input_qubit_mapping={qubit: index for index, qubit in enumerate(value.qubits)},
    )
    before_circuit, before_snapshot = value.copy(), copy.deepcopy(data)
    before_operations = [(item.operation.name, list(item.operation.params)) for item in value.data]
    timing_components(value, data, archived_circuit_seconds=21e-9)
    assert value == before_circuit and value.metadata == before_circuit.metadata
    assert value.layout == before_circuit.layout
    assert before_operations == [
        (item.operation.name, list(item.operation.params)) for item in value.data
    ]
    assert data == before_snapshot


def test_unbound_parameters_are_not_an_actual_frozen_circuit() -> None:
    value = QuantumCircuit(2)
    value.rz(Parameter("angle"), 0)
    data = snapshot()
    data["instructions"].append({"name": "rz", "qubits": [0], "duration_seconds": 0.0})
    result = timing_components(value, data)
    assert result["status"] == "BLOCKED" and result["full_critical_seconds"] is None
    assert any("Unbound" in item for item in result["missing"])


def test_finite_instruction_durations_that_overflow_cannot_emit_nonfinite_json() -> None:
    value = QuantumCircuit(2)
    value.x(0)
    value.x(0)
    data = snapshot()
    data["instructions"][0]["duration_seconds"] = 1e308
    result = timing_components(value, data)
    assert result["status"] == "BLOCKED" and result["full_critical_seconds"] is None
    json.dumps(result, allow_nan=False)
