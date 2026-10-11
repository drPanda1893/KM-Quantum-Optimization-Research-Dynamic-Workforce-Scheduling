"""Ideal-only matched-budget training; noisy execution is a separate data path."""

from __future__ import annotations

import json
from functools import lru_cache
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector
from scipy.optimize import minimize

from km_quantum.m4.models import M4Config
from km_quantum.m4.warm_start import build_warm_qaoa
from km_quantum.qaoa.analysis import StateTable, analysis_for_probabilities, build_state_table
from km_quantum.qaoa.models import QAOAConfig
from km_quantum.qaoa.operators import verify_operator
from km_quantum.qaoa.references import FrozenReference, validate_reference
from km_quantum.qaoa.simulation import (
    ExactEvaluator,
    bind_parameters,
    circuit_metrics,
    environment_metadata,
    run_qaoa,
)
from km_quantum.qubo.ising import IsingModel


@lru_cache(maxsize=64)
def _full_operator_gate(ising_json: str, max_qubits: int) -> bool:
    return verify_operator(IsingModel.model_validate_json(ising_json), max_qubits)["passed"]


def _preparation_quality_gate(
    relaxation: dict[str, Any] | None, config: M4Config, number_variables: int
) -> dict[str, Any]:
    if (
        relaxation is None
        or relaxation.get("status") != "CONVERGED"
        or relaxation.get("library_success") is not True
    ):
        raise ValueError("Warm-start preparation did not converge; no fallback is allowed")
    residual = relaxation.get("scaled_projected_gradient_inf")
    gap = relaxation.get("scaled_convex_tangent_gap")
    threshold = config.preparation_residual_tolerance
    measured: list[float] = []
    for value in (residual, gap):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                "Warm-start preparation failed the frozen residual/tangent-gap quality gate"
            )
        number = float(value)
        if not isfinite(number) or not 0 <= number <= threshold:
            raise ValueError(
                "Warm-start preparation failed the frozen residual/tangent-gap quality gate"
            )
        measured.append(number)
    try:
        vector = np.asarray(relaxation["y"], dtype=np.float64)
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("Warm-start preparation must contain a finite box vector y") from error
    if (
        vector.shape != (number_variables,)
        or not np.all(np.isfinite(vector))
        or np.any(vector < 0)
        or np.any(vector > 1)
    ):
        raise ValueError("Warm-start preparation must contain a finite box vector y")
    return {
        "passed": True,
        "threshold": threshold,
        "scaled_projected_gradient_inf": measured[0],
        "scaled_convex_tangent_gap": measured[1],
        "numerically_stationary_at_solver_gtol": relaxation.get("numerically_stationary"),
        "criterion": "library convergence plus both scaled residual and tangent gap <= threshold",
        "certificate": "numerical quality gate; not a rigorous symbolic certificate",
    }


def train_variant(
    reference: FrozenReference,
    *,
    algorithm: Literal["standard", "warm_start"],
    p: int,
    seed: int,
    config: M4Config,
    trace_path: Path,
    relaxation: dict[str, Any] | None = None,
    table: StateTable | None = None,
) -> tuple[dict[str, Any], QuantumCircuit, NDArray[np.float64]]:
    """Keep the M3 standard engine intact and train warm-start on exact energy only.

    The optimizer budget excludes one transparent final verification call, which
    cannot change the returned angles. Failed classical preparation is rejected
    before any trace or optimization can start.
    """
    total_started = perf_counter()
    if algorithm not in ("standard", "warm_start"):
        raise ValueError("algorithm must be standard or warm_start")
    if type(p) is not int or p not in config.depths:
        raise ValueError("The depth is outside the frozen M4 configuration")
    if type(seed) is not int or seed not in config.restart_seeds:
        raise ValueError("The restart seed is outside the frozen M4 configuration")
    if trace_path.exists():
        raise ValueError(f"Trace already exists: {trace_path}")
    preparation_quality = (
        _preparation_quality_gate(relaxation, config, reference.qubo.number_variables)
        if algorithm == "warm_start"
        else None
    )
    preparation_started = perf_counter()
    validate_reference(reference)
    if reference.qubo.number_variables > config.max_qubits:
        raise ValueError("The reference exceeds the frozen M4 qubit limit")
    table = table or build_state_table(reference, max_qubits=config.max_qubits)
    if table.reference_hash != reference.reference_hash or not table.checked_ground_set:
        raise ValueError("The state table belongs to a different or unchecked frozen reference")
    qaoa_config = QAOAConfig(
        p=p,
        initialization_seed=seed,
        maxiter=config.optimizer_maxiter,
        tolerance=config.optimizer_tolerance,
        rhobeg=config.optimizer_rhobeg,
        shots=config.shots,
        sampler_seed=config.sample_seed_base + 100 * p + seed,
        max_qubits=config.max_qubits,
    )
    if algorithm == "standard":
        preparation_ms = (perf_counter() - preparation_started) * 1000
        core_started = perf_counter()
        run, bound, probabilities = run_qaoa(
            reference, qaoa_config, trace_path=trace_path, table=table
        )
        legacy_core_ms = (perf_counter() - core_started) * 1000
        records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        last = records[-1]
        optimization_ms = max(0.0, last["elapsed_ms"] - last["evaluation_runtime_ms"])
        verification_ms = last["evaluation_runtime_ms"]
        internal_setup_and_analysis_ms = max(0.0, run["runtime_ms"] - last["elapsed_ms"])
        # A separate, measured M4 gate checks the returned distribution independently again.
        analysis_started = perf_counter()
        verified_analysis = analysis_for_probabilities(
            reference, table, probabilities, run["final_expected_energy"]
        )
        final_analysis_ms = (perf_counter() - analysis_started) * 1000
        run.update(verified_analysis)
        timings: dict[str, Any] = {
            "preparation_runtime_ms": preparation_ms,
            "optimization_runtime_ms": optimization_ms,
            "final_verification_runtime_ms": verification_ms,
            "final_analysis_runtime_ms": final_analysis_ms,
            "legacy_core_runtime_ms": legacy_core_ms,
            "legacy_internal_setup_and_analysis_runtime_ms": internal_setup_and_analysis_ms,
            "scope": (
                "External M4 preparation and second distribution gate measured directly; "
                "optimizer phase derived from M3 evaluator timestamps. Legacy core includes "
                "its own setup, training, verification and first final state analysis; "
                "its setup and first analysis remain combined. Fields are not additive."
            ),
        }
    else:
        assert relaxation is not None
        if not _full_operator_gate(reference.ising.model_dump_json(), config.max_qubits):
            raise ValueError("The full basis-state operator gate must pass before warm-start QAOA")
        prepared = build_warm_qaoa(
            reference.ising,
            p,
            relaxation["y"],
            epsilon=config.epsilon,
            max_qubits=config.max_qubits,
        )
        initial = np.random.default_rng(seed).uniform(0.0, np.pi, len(prepared.parameters))
        if len(initial) and qaoa_config.maxiter < len(initial) + 2:
            raise ValueError("COBYLA maxiter must be at least parameter_count + 2")
        evaluator = ExactEvaluator(prepared, trace_path)
        preparation_ms = (perf_counter() - preparation_started) * 1000
        optimization_started = perf_counter()
        if len(initial):
            optimized = minimize(
                evaluator.evaluate,
                initial,
                method="COBYLA",
                options={
                    "maxiter": qaoa_config.maxiter,
                    "tol": qaoa_config.tolerance,
                    "rhobeg": qaoa_config.rhobeg,
                },
            )
            final = np.asarray(optimized.x, dtype=np.float64)
            nfev = int(optimized.nfev)
            success, message = bool(optimized.success), str(optimized.message)
        else:
            final, nfev, success, message = initial, 0, True, "Trivial parameter-free circuit"
        optimization_ms = (perf_counter() - optimization_started) * 1000
        verification_started = perf_counter()
        expected = evaluator.evaluate(final, phase="verification")
        verification_ms = (perf_counter() - verification_started) * 1000
        analysis_started = perf_counter()
        bound = bind_parameters(prepared, final)
        probabilities = np.asarray(
            Statevector.from_instruction(bound).probabilities(), dtype=np.float64
        )
        verified_analysis = analysis_for_probabilities(reference, table, probabilities, expected)
        final_analysis_ms = (perf_counter() - analysis_started) * 1000
        parameter_order = [parameter.name for parameter in prepared.parameters]
        run = {
            "status": "COMPLETE",
            "reference_name": reference.name,
            "instance_id": reference.instance.instance_id,
            "m2_reference_hash": reference.reference_hash,
            "qubo_hash": reference.qubo_hash,
            "ising_hash": reference.ising_hash,
            "qubit_count": reference.qubo.number_variables,
            "p": p,
            "parameter_count": len(prepared.parameters),
            "parameter_order": parameter_order,
            "initialization_seed": seed,
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
            "config": qaoa_config.model_dump(mode="json"),
            "optimizer": "COBYLA",
            "optimizer_config": {
                "maxiter": qaoa_config.maxiter,
                "tolerance": qaoa_config.tolerance,
                "rhobeg": qaoa_config.rhobeg,
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
            "trace_path": str(trace_path),
            "environment": environment_metadata(),
            "operator_equivalence": True,
            **verified_analysis,
        }
        timings = {
            "preparation_runtime_ms": preparation_ms,
            "optimization_runtime_ms": optimization_ms,
            "final_verification_runtime_ms": verification_ms,
            "final_analysis_runtime_ms": final_analysis_ms,
            "scope": (
                "Warm circuit preparation/operator gate, ideal optimization, final energy "
                "verification and final statevector analysis measured separately. "
                "Classical relaxation time is separately reported, not included here."
            ),
        }
    run.update(
        {
            "schema_version": 4,
            "artifact_type": "m4_ideal_training",
            "run_id": f"{reference.name}-{algorithm}-p{p}-seed{seed}",
            "algorithm": algorithm,
            "relaxation": relaxation if algorithm == "warm_start" else None,
            "preparation_quality_gate": preparation_quality,
            "epsilon": config.epsilon if algorithm == "warm_start" else None,
            "cost_evolution": "exp(-i*gamma*H_cost)",
            "mixer_evolution": "exp(-i*beta*H_mixer)",
            "mixer_sign": 1,
            "hamiltonian_scale": "unchanged frozen M2 coefficients",
            "training_cost": "ideal exact StatevectorEstimator; no shots or channel noise",
            "timings": timings,
            "runtime_ms": (perf_counter() - total_started) * 1000,
        }
    )
    json.dumps(run, allow_nan=False)
    return run, bound, probabilities
