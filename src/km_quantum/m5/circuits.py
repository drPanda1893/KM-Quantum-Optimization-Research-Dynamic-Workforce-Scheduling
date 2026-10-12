"""Reconstruct frozen M4 circuits and compile one documented hardware representation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector
from qiskit.transpiler import Target
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from threadpoolctl import threadpool_limits

from km_quantum.m4.metrics import METRIC_MASKS, analyze_distribution, build_plan_table
from km_quantum.m4.warm_start import build_warm_qaoa, warm_initial_state
from km_quantum.qaoa.references import FrozenReference, load_references
from km_quantum.qaoa.simulation import bind_parameters, build_qaoa, uniform_initial_state

from .mapping import (
    MeasurementMapping,
    compact_simulation_copy,
    mapping_from_compiled,
    marginal_m2_probabilities,
    terminal_measurements,
)
from .snapshots import restrict_target

CASES = ("A_single_job", "E_overlap", "H_workload", "K_bounded_slack")
LOGICAL_WIDTHS = {"A_single_job": 2, "E_overlap": 4, "H_workload": 5, "K_bounded_slack": 7}
PROBABILITY_ATOL = 1e-10
ENERGY_ATOL = 1e-8
TRANSPILE_SEED = 424242


@dataclass(frozen=True)
class FrozenCircuit:
    circuit_id: str
    reference_name: str
    algorithm: str
    p: int | None
    seed: int | None
    circuit: QuantumCircuit
    ideal_probabilities: NDArray[np.float64]
    source_hashes: dict[str, Any]
    reference: FrozenReference
    frozen_parameters: tuple[float, ...] = ()
    parameter_order: tuple[str, ...] = ()
    relaxation_y: tuple[float, ...] = ()
    epsilon: float = 0.25


@dataclass(frozen=True)
class HardwareCompilation:
    provider_circuit: QuantumCircuit
    compact_circuit: QuantumCircuit
    mapping: MeasurementMapping
    active_physical_qubits: tuple[int, ...]
    summary: dict[str, Any]

    def unmeasured_compact(self) -> QuantumCircuit:
        return self.compact_circuit.remove_final_measurements(inplace=False)


def _read_source(path: Path, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = path.read_bytes()
    document = json.loads(raw)
    return document, {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "lf_sha256": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
        "line_ending_normalization_applies_only_to_text": True,
    }


def _check_source_identity(record: dict[str, Any], reference: FrozenReference) -> None:
    for key, expected in (
        ("reference_id", reference.name),
        ("reference_hash", reference.reference_hash),
        ("qubo_hash", reference.qubo_hash),
        ("ising_hash", reference.ising_hash),
        ("qubit_count", reference.qubo.number_variables),
    ):
        if record.get(key) != expected:
            raise ValueError(f"Frozen M4 source identity mismatch: {key}")
    if record.get("status") != "COMPLETE":
        raise ValueError("Frozen M4 source must be complete")


def logical_probabilities(circuit: QuantumCircuit) -> NDArray[np.float64]:
    if circuit.num_qubits > 10 or circuit.num_clbits or circuit.num_parameters:
        raise ValueError("Ideal logical simulation requires a bounded unmeasured bound circuit")
    with threadpool_limits(limits=2):
        return np.asarray(Statevector.from_instruction(circuit).probabilities(), dtype=np.float64)


def reconstruct_suite(root: str | Path) -> tuple[FrozenCircuit, ...]:
    """Read seed-0 final angles and existing relaxation vectors; never call an optimizer."""
    repository = Path(root)
    references = load_references(repository)
    result = []
    for name in CASES:
        reference = references[name]
        if reference.qubo.number_variables != LOGICAL_WIDTHS[name]:
            raise ValueError("The predeclared M5 logical suite dimensions changed")
        preparation, preparation_hash = _read_source(
            repository / "results/m4/preparations" / f"{name}.json", repository
        )
        _check_source_identity(preparation, reference)
        relaxation = preparation["relaxation"]
        if relaxation.get("status") != "CONVERGED" or relaxation.get("library_success") is not True:
            raise ValueError("Frozen warm preparation did not converge")
        y = tuple(float(value) for value in relaxation["y"])
        for p in (1, 2):
            for algorithm in ("standard", "warm_start"):
                training_id = f"{name}-{algorithm}-p{p}-seed0"
                training, training_hash = _read_source(
                    repository / "results/m4/training" / f"{training_id}.json", repository
                )
                _check_source_identity(training, reference)
                if (
                    training.get("training_id") != training_id
                    or training.get("p") != p
                    or training.get("algorithm") != algorithm
                    or training.get("initialization_seed") != 0
                ):
                    raise ValueError("M5 only transfers the predeclared M4 seed-0 runs")
                if algorithm == "warm_start":
                    if training.get("epsilon") != 0.25 or training.get("relaxation") != relaxation:
                        raise ValueError("Frozen epsilon or relaxation differs from preparation")
                    prepared = build_warm_qaoa(reference.ising, p, y, epsilon=0.25, max_qubits=10)
                else:
                    prepared = build_qaoa(reference.ising, p, max_qubits=10)
                order = tuple(parameter.name for parameter in prepared.parameters)
                if order != tuple(training["parameter_order"]):
                    raise ValueError("Reconstructed actual parameter order differs from M4")
                values = tuple(float(value) for value in training["final_parameters"])
                bindings = training["parameter_bindings"]
                if len(bindings) != len(values) or any(
                    binding["position"] != index
                    or binding["name"] != order[index]
                    or binding["final_value"] != values[index]
                    for index, binding in enumerate(bindings)
                ):
                    raise ValueError("Frozen parameter binding records differ from final angles")
                circuit = bind_parameters(prepared, values)
                circuit.name = training_id
                probabilities = np.asarray(training["ideal_probabilities"], dtype=np.float64)
                actual = logical_probabilities(circuit)
                if not np.allclose(actual, probabilities, rtol=0, atol=PROBABILITY_ATOL):
                    raise ValueError("Frozen M4 distribution reconstruction failed")
                table = build_plan_table(reference)
                metrics = analyze_distribution(reference, table, actual, include_plan_rows=False)
                if (
                    abs(metrics["expected_qubo_energy"] - training["final_expected_energy"])
                    > ENERGY_ATOL
                ):
                    raise ValueError("Frozen M4 offset-inclusive energy reconstruction failed")
                for metric in METRIC_MASKS:
                    if abs(metrics[metric] - training["ideal_metrics"][metric]) > PROBABILITY_ATOL:
                        raise ValueError(
                            f"Frozen M4 probability metric reconstruction failed: {metric}"
                        )
                probabilities.flags.writeable = False
                result.append(
                    FrozenCircuit(
                        training_id,
                        name,
                        algorithm,
                        p,
                        0,
                        circuit,
                        probabilities,
                        {
                            "training": training_hash,
                            "preparation": preparation_hash,
                            "m4_training_id": training_id,
                            "reference_hash": reference.reference_hash,
                            "qubo_hash": reference.qubo_hash,
                            "ising_hash": reference.ising_hash,
                        },
                        reference,
                        values,
                        order,
                        y,
                    )
                )
        for algorithm in ("uniform", "warm_product"):
            control, control_hash = _read_source(
                repository / "results/m4/controls" / f"{name}-{algorithm}.json", repository
            )
            _check_source_identity(control, reference)
            if control.get("control") != algorithm or control.get("relaxation") != relaxation:
                raise ValueError("Frozen control identity or relaxation changed")
            circuit = (
                uniform_initial_state(reference.qubo.number_variables)
                if algorithm == "uniform"
                else warm_initial_state(y, epsilon=0.25)
            )
            circuit.name = f"{name}-{algorithm}"
            probabilities = np.asarray(control["probabilities"], dtype=np.float64)
            if not np.allclose(
                logical_probabilities(circuit), probabilities, rtol=0, atol=PROBABILITY_ATOL
            ):
                raise ValueError("Frozen M4 control distribution reconstruction failed")
            probabilities.flags.writeable = False
            result.append(
                FrozenCircuit(
                    circuit.name,
                    name,
                    algorithm,
                    None,
                    None,
                    circuit,
                    probabilities,
                    {
                        "control": control_hash,
                        "preparation": preparation_hash,
                        "reference_hash": reference.reference_hash,
                        "qubo_hash": reference.qubo_hash,
                        "ising_hash": reference.ising_hash,
                    },
                    reference,
                    relaxation_y=y,
                )
            )
    if len(result) != 24 or len({item.circuit_id for item in result}) != 24:
        raise ValueError("M5 requires exactly sixteen fixed QAOA circuits and eight controls")
    return tuple(result)


def smoke_circuits(number_qubits: int = 7) -> tuple[QuantumCircuit, ...]:
    if number_qubits != 7:
        raise ValueError("The predeclared smoke register has exactly seven bits")
    patterns = ("0000000", "1111111", "1001010", "0110001")
    result = []
    for index, pattern in enumerate(patterns):
        circuit = QuantumCircuit(number_qubits, name=f"smoke-{index}-{pattern}")
        for variable, bit in enumerate(pattern):
            if bit == "1":
                circuit.x(variable)
        circuit.metadata = {"artifact_type": "m5_smoke_basis_control", "expected_m2_bits": pattern}
        result.append(circuit)
    return tuple(result)


def validate_native_instructions(circuit: QuantumCircuit, target: Target) -> None:
    for item in circuit.data:
        name = item.operation.name
        if name == "barrier":
            continue
        qubits = tuple(circuit.find_bit(bit).index for bit in item.qubits)
        if not target.instruction_supported(
            operation_name=name, qargs=qubits, parameters=item.operation.params
        ):
            raise ValueError(
                f"Native circuit uses unsupported Target instruction/direction: {name}{qubits}"
            )


def compile_hardware(
    logical: QuantumCircuit,
    target: Target,
    physical_subset: Sequence[int],
    *,
    optimization_level: int = 1,
    seed_transpiler: int = TRANSPILE_SEED,
    max_active_qubits: int = 10,
    m2_to_classical: Sequence[int] | None = None,
) -> HardwareCompilation:
    if optimization_level != 1 or seed_transpiler != TRANSPILE_SEED:
        raise ValueError("M5 compiler policy is frozen at optimization_level=1, seed=424242")
    if logical.num_qubits > 7 or logical.num_parameters:
        raise ValueError("M5 hardware transfer requires at most seven fully bound logical qubits")
    subset = tuple(physical_subset)
    restricted = restrict_target(target, subset)
    initial = subset[: logical.num_qubits]
    measured = terminal_measurements(logical, m2_to_classical=m2_to_classical)
    manager = generate_preset_pass_manager(
        optimization_level=optimization_level,
        target=restricted,
        initial_layout=list(initial),
        seed_transpiler=seed_transpiler,
        approximation_degree=1.0,
    )
    provider = manager.run(measured)
    validate_native_instructions(provider, target)
    mapping = mapping_from_compiled(provider, initial, m2_to_classical=m2_to_classical)
    if not set(mapping.active_physical_qubits).issubset(subset):
        raise ValueError("Compiler routing escaped the frozen physical subset")
    compact = compact_simulation_copy(provider, mapping, max_active_qubits=max_active_qubits)
    histogram = dict(sorted(provider.count_ops().items()))
    summary = {
        "optimization_level": optimization_level,
        "seed_transpiler": seed_transpiler,
        "approximation_degree": 1.0,
        "physical_subset": list(subset),
        "initial_layout": list(initial),
        "routing_confined_to_subset": True,
        "provider_width": provider.num_qubits,
        "active_width": compact.num_qubits,
        "active_physical_qubits": list(mapping.active_physical_qubits),
        "depth": provider.depth(),
        "gate_histogram": histogram,
        "one_qubit_gate_count": sum(
            len(item.qubits) == 1 and item.operation.name != "measure" for item in provider.data
        ),
        "two_qubit_gate_count": sum(
            len(item.qubits) == 2 and item.operation.name != "barrier" for item in provider.data
        ),
        "measurement_count": histogram.get("measure", 0),
        "mapping": mapping.to_dict(),
        "target_validation_passed": True,
        "provider_circuit_modified_for_simulation": False,
        "qiskit_version": version("qiskit"),
        "global_phase": float(provider.global_phase),
        "native_instructions": [
            {
                "index": index,
                "name": item.operation.name,
                "physical_qubits": [provider.find_bit(bit).index for bit in item.qubits],
                "classical_bits": [provider.find_bit(bit).index for bit in item.clbits],
                "parameters": [float(parameter) for parameter in item.operation.params],
            }
            for index, item in enumerate(provider.data)
        ],
    }
    return HardwareCompilation(provider, compact, mapping, mapping.active_physical_qubits, summary)


def compiled_ideal_probabilities(compilation: HardwareCompilation) -> NDArray[np.float64]:
    unmeasured = compilation.unmeasured_compact()
    return marginal_m2_probabilities(logical_probabilities(unmeasured), compilation.mapping)


def verify_compilation(
    frozen: FrozenCircuit,
    compilation: HardwareCompilation,
) -> dict[str, Any]:
    probabilities = compiled_ideal_probabilities(compilation)
    delta = float(np.max(np.abs(probabilities - frozen.ideal_probabilities)))
    if delta > PROBABILITY_ATOL:
        raise ValueError("Hardware compilation ideal register distribution differs from frozen M4")
    table = build_plan_table(frozen.reference)
    ideal = analyze_distribution(
        frozen.reference, table, frozen.ideal_probabilities, include_plan_rows=False
    )
    compiled = analyze_distribution(frozen.reference, table, probabilities, include_plan_rows=False)
    energy_delta = abs(compiled["expected_qubo_energy"] - ideal["expected_qubo_energy"])
    if energy_delta > ENERGY_ATOL:
        raise ValueError("Hardware compilation offset-inclusive QUBO energy differs")
    differences = {metric: abs(compiled[metric] - ideal[metric]) for metric in METRIC_MASKS}
    if any(difference > PROBABILITY_ATOL for difference in differences.values()):
        raise ValueError("Hardware compilation changes an M1/M2 probability metric")
    return {
        "passed": True,
        "probability_atol": PROBABILITY_ATOL,
        "energy_atol": ENERGY_ATOL,
        "maximum_probability_absolute_difference": delta,
        "expected_qubo_energy_absolute_difference": energy_delta,
        "metric_absolute_differences": differences,
        "logical_ideal_metrics": ideal,
        "compiled_ideal_metrics": compiled,
    }
