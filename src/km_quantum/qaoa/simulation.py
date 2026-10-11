"""Visible standard-QAOA loop using local, exact V2 statevector primitives."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
from qiskit.circuit.library import qaoa_ansatz
from qiskit.primitives import StatevectorEstimator
from qiskit.quantum_info import Statevector
from scipy.optimize import minimize

from km_quantum.qaoa.analysis import StateTable, analysis_for_probabilities, build_state_table
from km_quantum.qaoa.models import Evaluation, QAOAConfig
from km_quantum.qaoa.operators import IsingOperator, ising_to_operator, verify_operator
from km_quantum.qaoa.references import FrozenReference, validate_reference
from km_quantum.qubo.ising import IsingModel


@dataclass(frozen=True)
class PreparedQAOA:
    circuit: QuantumCircuit
    parameters: tuple[Parameter, ...]
    operator: IsingOperator


def uniform_initial_state(number_qubits: int) -> QuantumCircuit:
    if type(number_qubits) is not int or number_qubits < 0:
        raise ValueError("number_qubits must be a nonnegative integer")
    circuit = QuantumCircuit(number_qubits)
    for index in range(number_qubits):
        circuit.h(index)
    return circuit


def build_qaoa(ising: IsingModel, p: int, *, max_qubits: int = 14) -> PreparedQAOA:
    """Defaults implement |+> and sum X; no schedule enters circuit construction."""
    if type(p) is not int or not 1 <= p <= 3:
        raise ValueError("M3 supports p=1,2,3")
    if type(max_qubits) is not int or max_qubits < 0 or ising.number_variables > max_qubits:
        raise ValueError("The Ising model exceeds max_qubits")
    operator = ising_to_operator(ising)
    if ising.number_variables == 0:
        circuit = QuantumCircuit(0)
    elif not ising.fields and not ising.couplings:
        circuit = uniform_initial_state(ising.number_variables)
    else:
        circuit = qaoa_ansatz(operator.circuit_hamiltonian, reps=p, flatten=True)
    # Read actual parameter objects; never assume a beta/gamma vector ordering.
    return PreparedQAOA(circuit, tuple(circuit.parameters), operator)


def bind_parameters(
    prepared: PreparedQAOA, values: Sequence[float] | NDArray[np.float64]
) -> QuantumCircuit:
    point = np.asarray(values, dtype=float)
    if point.shape != (len(prepared.parameters),) or not np.all(np.isfinite(point)):
        raise ValueError("Parameters must be a finite vector matching circuit.parameters")
    return prepared.circuit.assign_parameters(
        {
            parameter: float(value)
            for parameter, value in zip(prepared.parameters, point, strict=True)
        }
    )


def circuit_metrics(prepared: PreparedQAOA) -> dict[str, Any]:
    # Synthesize logical evolution gates only; no hardware target or routing.
    logical = prepared.circuit.decompose(reps=3)
    return {
        "number_qubits": prepared.circuit.num_qubits,
        "parameter_count": len(prepared.parameters),
        "logical_depth": logical.depth(),
        "gate_count": len(logical.data),
        "one_qubit_gate_count": sum(len(item.qubits) == 1 for item in logical.data),
        "two_qubit_gate_count": sum(len(item.qubits) == 2 for item in logical.data),
        "other_gate_count": sum(len(item.qubits) > 2 for item in logical.data),
        "gate_histogram": dict(sorted(logical.count_ops().items())),
        "parameter_order": [parameter.name for parameter in prepared.parameters],
        "hardware_transpilation": False,
        "logical_decomposition_repetitions": 3,
    }


class ExactEvaluator:
    def __init__(self, prepared: PreparedQAOA, trace_path: str | Path | None = None) -> None:
        self.prepared = prepared
        self.estimator = StatevectorEstimator(default_precision=0.0)
        self.started = perf_counter()
        self.trace: list[Evaluation] = []
        self.trace_path = Path(trace_path) if trace_path is not None else None
        if self.trace_path is not None:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            if self.trace_path.exists():
                raise ValueError(f"Trace already exists: {self.trace_path}")
            self.trace_path.touch()

    def evaluate(
        self,
        values: Sequence[float] | NDArray[np.float64],
        *,
        phase: Literal["optimizer", "verification"] = "optimizer",
    ) -> float:
        started = perf_counter()
        bound = bind_parameters(self.prepared, values)
        if bound.num_qubits and np.any(self.prepared.operator.circuit_hamiltonian.coeffs):
            result = self.estimator.run(
                [(bound, self.prepared.operator.circuit_hamiltonian)]
            ).result()
            expectation = float(np.asarray(result[0].data.evs).item())
        else:
            expectation = 0.0
        energy = expectation + self.prepared.operator.energy_offset
        if not np.isfinite(energy):
            raise ValueError("Estimator returned a nonfinite energy")
        record = Evaluation(
            evaluation=len(self.trace) + 1,
            phase=phase,
            parameters=tuple(float(value) for value in values),
            expectation=expectation,
            expected_energy=energy,
            elapsed_ms=(perf_counter() - self.started) * 1000,
            evaluation_runtime_ms=(perf_counter() - started) * 1000,
        )
        self.trace.append(record)
        if self.trace_path is not None:
            with self.trace_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(record.model_dump_json() + "\n")
        return energy


def environment_metadata(root: Path | None = None) -> dict[str, Any]:
    root = root or Path.cwd()
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    lock = root / "uv.lock"
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "qiskit": version("qiskit"),
        "scipy": version("scipy"),
        "numpy": version("numpy"),
        "git_commit": commit,
        "git_dirty": dirty,
        "dependency_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest()
        if lock.exists()
        else None,
    }


@lru_cache(maxsize=256)
def _verified_operator(ising_json: str, max_qubits: int) -> bool:
    """Cache only a verification actually executed against this complete model."""
    report = verify_operator(IsingModel.model_validate_json(ising_json), max_qubits)
    return report["passed"]


def run_qaoa(
    reference: FrozenReference,
    config: QAOAConfig | None = None,
    *,
    trace_path: str | Path | None = None,
    table: StateTable | None = None,
) -> tuple[dict[str, Any], QuantumCircuit, np.ndarray]:
    """Train on exact energy, then verify the complete final distribution.

    A nonconverged COBYLA restart remains an honest scientific observation.
    The budget covers optimizer calls; one separately logged final verification
    call does not train or change the returned parameters.
    """
    started = perf_counter()
    config = config or QAOAConfig()
    validate_reference(reference)
    if not _verified_operator(reference.ising.model_dump_json(), config.max_qubits):
        raise ValueError("The full basis-state operator gate must pass before QAOA")
    table = table or build_state_table(reference, max_qubits=config.max_qubits)
    if getattr(table, "reference_hash", reference.reference_hash) != reference.reference_hash:
        raise ValueError("The state table belongs to a different frozen reference")
    prepared = build_qaoa(reference.ising, config.p, max_qubits=config.max_qubits)
    rng = np.random.default_rng(config.initialization_seed)
    initial = rng.uniform(0.0, np.pi, len(prepared.parameters))
    if len(initial) and config.maxiter < len(initial) + 2:
        raise ValueError("COBYLA maxiter must be at least parameter_count + 2")
    evaluator = ExactEvaluator(prepared, trace_path)
    if len(initial):
        optimized = minimize(
            evaluator.evaluate,
            initial,
            method="COBYLA",
            options={"maxiter": config.maxiter, "tol": config.tolerance, "rhobeg": config.rhobeg},
        )
        final = np.asarray(optimized.x, dtype=float)
        nfev = int(optimized.nfev)
        success = bool(optimized.success)
        message = str(optimized.message)
    else:
        final, nfev, success, message = initial, 0, True, "Trivial parameter-free circuit"
    expected = evaluator.evaluate(final, phase="verification")
    bound = bind_parameters(prepared, final)
    probabilities = np.asarray(Statevector.from_instruction(bound).probabilities(), dtype=float)
    analysis = analysis_for_probabilities(reference, table, probabilities, expected)
    parameter_order = [parameter.name for parameter in prepared.parameters]
    run: dict[str, Any] = {
        "schema_version": 3,
        "artifact_type": "m3_ideal_qaoa",
        "status": "COMPLETE",
        "run_id": f"{reference.name}-p{config.p}-seed{config.initialization_seed}",
        "reference_name": reference.name,
        "instance_id": reference.instance.instance_id,
        "m2_reference_hash": reference.reference_hash,
        "qubo_hash": reference.qubo_hash,
        "ising_hash": reference.ising_hash,
        "qubit_count": reference.qubo.number_variables,
        "p": config.p,
        "parameter_count": len(prepared.parameters),
        "parameter_order": parameter_order,
        "initialization_seed": config.initialization_seed,
        "initial_parameters": initial.tolist(),
        "final_parameters": final.tolist(),
        "parameter_bindings": [
            {
                "position": index,
                "name": name,
                "initial_value": float(initial[index]),
                "final_value": float(final[index]),
            }
            for index, name in enumerate(parameter_order)
        ],
        "config": config.model_dump(mode="json"),
        "optimizer": "COBYLA",
        "optimizer_config": {
            "maxiter": config.maxiter,
            "tolerance": config.tolerance,
            "rhobeg": config.rhobeg,
        },
        "nfev": nfev,
        "success": success,
        "termination_message": message,
        "verification_evaluations": 1,
        "total_expectation_evaluations": len(evaluator.trace),
        "initial_expected_energy": evaluator.trace[0].expected_energy,
        "final_expected_energy": expected,
        "energy_offset": prepared.operator.energy_offset,
        "ground_state_count": len(reference.ground_bits),
        "circuit": circuit_metrics(prepared),
        "structural_metadata": reference.qubo.metrics().model_dump(mode="json"),
        "constraint_energy_margin": reference.constraint_energy_margin,
        "trace_path": str(trace_path) if trace_path else None,
        "environment": environment_metadata(),
        "operator_equivalence": True,
        **analysis,
    }
    run["runtime_ms"] = (perf_counter() - started) * 1000
    # Also check JSON serializability and reject any accidental NaN/Inf fields.
    json.dumps(run, allow_nan=False)
    return run, bound, probabilities
