"""Executed correctness gates before any M4 quality comparison is permitted."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator, Statevector
from scipy.linalg import expm

from km_quantum.m4.io import (
    canonical_hash,
    lf_file_hash,
    read_json,
    verify_reference_integrity,
    write_json,
)
from km_quantum.m4.metrics import M3_METRIC_REMAP, build_plan_table
from km_quantum.m4.models import M4Config
from km_quantum.m4.noise import (
    DEFAULT_NOISE_PROFILES,
    CompilerConfig,
    NoiseProfile,
    ResourceLimits,
    apply_readout,
    compile_bound_circuit,
    resource_preflight,
    simulate_density,
)
from km_quantum.m4.suite import load_m4_references
from km_quantum.m4.warm_start import (
    M3_MIXER_SIGN,
    build_warm_qaoa,
    warm_initial_state,
    warm_mixer_circuit,
)
from km_quantum.qaoa.operators import verify_operator
from km_quantum.qaoa.references import FrozenReference
from km_quantum.qaoa.simulation import bind_parameters, build_qaoa

VERIFICATION_ATOL = 1e-9


def _state_difference(first: np.ndarray, second: np.ndarray) -> float:
    """Compare amplitudes modulo one global phase using a nonzero anchor."""
    anchor = int(np.argmax(np.abs(first)))
    phase = second[anchor] / first[anchor]
    if abs(phase) == 0:
        return float("inf")
    phase /= abs(phase)
    return float(np.max(np.abs(second - phase * first)))


def _historical_audits(root: Path, integrity: dict[str, Any]) -> dict[str, Any]:
    m3 = read_json(root / "results/m4/preflight/m3_reverification.json")
    semantics = read_json(root / "results/m4/metric_semantics_audit.json")
    if (
        m3.get("status") != "PASS"
        or m3.get("m3_reference_commit") != integrity["m3_reference_commit"]
        or m3.get("saved_runs_reconstructed_and_audited") != 660
        or m3.get("operator_checked_states") != 14246
        or m3.get("operator_references") != 113
        or m3.get("historical_artifacts_modified") is not False
        or m3.get("m2_provenance", {}).get("unchanged") is not True
    ):
        raise ValueError("Historical M3 reverification disagrees with frozen M3 provenance")
    if (
        semantics.get("status") != "PASS"
        or semantics.get("checked_frozen_references") != 114
        or semantics.get("checked_basis_states") != 47014
        or semantics.get("m3_saved_rows") != 660
        or semantics.get("historical_m3_files_modified") is not False
        or semantics.get("historical_m3_algorithm_or_mapping_error") is not False
        or semantics.get("metric_remap") != M3_METRIC_REMAP
    ):
        raise ValueError("Historical metric-semantics audit is missing or inconsistent")
    rows = {row["run_id"]: row for row in semantics.get("rows", [])}
    paths = sorted((root / "results/m3/runs").glob("*.json"))
    if len(rows) != 660 or len(paths) != 660:
        raise ValueError("Historical metric-semantics audit does not contain all unique M3 runs")
    for path in paths:
        saved = read_json(path)
        row = rows.get(saved["run_id"])
        if row is None or row.get("reference_id") != saved["reference_name"]:
            raise ValueError("Historical semantic row has a mismatched run/reference identity")
        if any(row.get(name) != saved[original] for name, original in M3_METRIC_REMAP.items()):
            raise ValueError("Historical semantic row changes a saved M3 probability")
        if not (
            row["p_ground_encoding"] <= row["p_schedule_optimal"] + 1e-12
            and row["p_schedule_optimal"] <= row["p_schedule_feasible"] + 1e-12
            and row["p_encoding_feasible"] <= row["p_schedule_feasible"] + 1e-12
        ):
            raise ValueError("Historical semantic probability containment fails")
    counterexamples = semantics.get("counterexamples", {})
    if not counterexamples:
        raise ValueError("The historical semantic audit lacks a documented counterexample")
    if not any(
        row["metrics_for_point_mass"]["p_ground_encoding"]
        < row["metrics_for_point_mass"]["p_schedule_optimal"]
        for row in counterexamples.values()
    ):
        raise ValueError("No auxiliary-bit counterexample disproves the probability equality")
    return {
        "m3_reverification_sha256": canonical_hash(m3),
        "metric_semantics_audit_sha256": canonical_hash(semantics),
        "m3_saved_rows_rechecked": len(paths),
        "m3_reference_commit": m3["m3_reference_commit"],
        "historical_algorithm_or_mapping_error": False,
        "historical_m3_files_modified": False,
        "counterexample_names": sorted(counterexamples),
    }


def _check_manifest(
    root: Path,
    manifest: dict[str, Any] | None,
    config: M4Config,
    references: dict[str, FrozenReference],
    integrity: dict[str, Any],
) -> None:
    if manifest is None:
        return
    if manifest.get("config_hash") != canonical_hash(config.model_dump(mode="json")):
        raise ValueError("Frozen M4 manifest configuration hash mismatch")
    if manifest.get("configuration") != config.model_dump(mode="json"):
        raise ValueError("Frozen M4 manifest configuration body mismatch")
    unsealed = {key: value for key, value in manifest.items() if key != "manifest_content_sha256"}
    if manifest.get("manifest_content_sha256") != canonical_hash(unsealed):
        raise ValueError("Frozen M4 manifest content seal mismatch")
    if manifest.get("noise_profiles") != [asdict(profile) for profile in DEFAULT_NOISE_PROFILES]:
        raise ValueError("Frozen M4 manifest noise profile mismatch")
    if manifest.get("compiler") != asdict(CompilerConfig(seed=config.transpiler_seed)):
        # JSON turns tuple basis_gates into a list.
        compiler_json = asdict(CompilerConfig(seed=config.transpiler_seed))
        compiler_json["basis_gates"] = list(compiler_json["basis_gates"])
        if manifest.get("compiler") != compiler_json:
            raise ValueError("Frozen M4 manifest compiler policy mismatch")
    if manifest.get("m3_reference_commit") != integrity["m3_reference_commit"]:
        raise ValueError("Frozen M4 manifest M3 commit mismatch")
    for relative, expected in manifest.get("inputs", {}).items():
        if lf_file_hash(root / relative) != expected:
            raise ValueError(f"Frozen M4 manifest input hash mismatch: {relative}")
    rows = manifest.get("references", [])
    selected = {row["reference_id"]: row for row in rows}
    if len(selected) != len(rows) or set(selected) != set(references):
        raise ValueError("Frozen M4 manifest reference selection mismatch")
    for name, reference in references.items():
        row = selected[name]
        if any(
            row.get(key) != expected
            for key, expected in (
                ("reference_hash", reference.reference_hash),
                ("qubo_hash", reference.qubo_hash),
                ("ising_hash", reference.ising_hash),
                ("qubit_count", reference.qubo.number_variables),
            )
        ):
            raise ValueError("Frozen M4 manifest reference/configuration identity mismatch")


def _mixer_matrix_gate() -> dict[str, Any]:
    x = np.array([[0.0, 1.0], [1.0, 0.0]])
    z = np.diag([1.0, -1.0])
    maximum_matrix_difference = maximum_probability_difference = maximum_state_difference = 0.0
    fixtures = []
    for y, beta in ((0.03, 0.19), (0.31, -0.47), (0.5, 0.71), (0.94, 1.13)):
        c = float(np.clip(y, 0.25, 0.75))
        theta = float(2 * np.arcsin(np.sqrt(c)))
        rotation = np.array(
            [[np.cos(theta / 2), -np.sin(theta / 2)], [np.sin(theta / 2), np.cos(theta / 2)]]
        )
        generator = rotation @ z @ rotation.T
        expected = expm(-1j * beta * generator)
        actual = np.asarray(Operator(warm_mixer_circuit([y], beta, epsilon=0.25)).data)
        difference = float(np.max(np.abs(actual - expected)))
        start = np.asarray(Statevector.from_instruction(warm_initial_state([y])).data)
        probability_difference = abs(float(abs(start[1]) ** 2) - c)
        preservation = _state_difference(start, actual @ start)
        maximum_matrix_difference = max(maximum_matrix_difference, difference)
        maximum_probability_difference = max(maximum_probability_difference, probability_difference)
        maximum_state_difference = max(maximum_state_difference, preservation)
        half = np.asarray(Operator(warm_mixer_circuit([y], beta, epsilon=0.5)).data)
        maximum_matrix_difference = max(
            maximum_matrix_difference,
            float(np.max(np.abs(half - expm(-1j * beta * x)))),
        )
        fixtures.append(
            {
                "y": y,
                "clipped_c": c,
                "theta": theta,
                "beta": beta,
                "matrix_max_absolute_difference": difference,
                "initial_p1_absolute_difference": probability_difference,
                "mixer_only_start_state_difference_up_to_phase": preservation,
            }
        )
    passed = (
        M3_MIXER_SIGN == 1
        and max(maximum_matrix_difference, maximum_probability_difference, maximum_state_difference)
        <= VERIFICATION_ATOL
    )
    return {
        "passed": passed,
        "m3_mixer_sign": M3_MIXER_SIGN,
        "matrix_exponential_sign": -1,
        "chronological_gate_order": ["RY(-theta)", "RZ(2*beta)", "RY(theta)"],
        "maximum_matrix_difference": maximum_matrix_difference,
        "maximum_initial_p1_difference": maximum_probability_difference,
        "maximum_mixer_only_state_difference_up_to_phase": maximum_state_difference,
        "fixtures": fixtures,
    }


def _readout_channel_gate(limits: ResourceLimits, compiler: CompilerConfig) -> dict[str, Any]:
    circuit = QuantumCircuit(1)
    circuit.x(0)
    density = simulate_density(
        compile_bound_circuit(circuit, compiler, limits),
        NoiseProfile("gate_fixture", 0.2, 0, 0),
        limits,
    )
    channel_difference = float(np.max(np.abs(density.probabilities_before_readout - [0.1, 0.9])))
    q0 = [[0.9, 0.1], [0.3, 0.7]]
    q1 = [[0.8, 0.2], [0.4, 0.6]]
    one = apply_readout([0, 1], [q0])
    two = apply_readout([0, 1, 0, 0], [q0, q1])
    one_difference = float(np.max(np.abs(one - [0.3, 0.7])))
    two_difference = float(np.max(np.abs(two - [0.24, 0.56, 0.06, 0.14])))
    return {
        "passed": max(channel_difference, one_difference, two_difference) <= VERIFICATION_ATOL,
        "depolarizing_1q_fixture_lambda": 0.2,
        "depolarizing_expected_probabilities": [0.1, 0.9],
        "depolarizing_actual_probabilities": density.probabilities_before_readout.tolist(),
        "depolarizing_max_absolute_difference": channel_difference,
        "one_qubit_readout_actual": one.tolist(),
        "one_qubit_readout_max_absolute_difference": one_difference,
        "two_qubit_readout_true_basis_index": 1,
        "two_qubit_readout_actual": two.tolist(),
        "two_qubit_readout_max_absolute_difference": two_difference,
        "matrix_orientation": "row=true, column=observed; multiply transpose",
        "qubit_order": "variable/qubit i has basis-index weight 2**i",
        "noise_gate_coverage": density.coverage,
    }


def verify_m4(
    root: Path,
    output: Path,
    config: M4Config,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute prerequisite gates and write additive verification evidence.

    Failure is written explicitly and prevents PASS; no variational optimization
    occurs here. Fixed arbitrary angles and uniform warm fixtures test mechanics
    without exposing new primary quality outcomes.
    """
    started = perf_counter()
    report: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "m4_technical_verification",
        "status": "FAIL",
        "config_hash": canonical_hash(config.model_dump(mode="json")),
        "manifest_hash": canonical_hash(manifest) if manifest is not None else None,
        "manifest_content_sha256": manifest.get("manifest_content_sha256")
        if manifest is not None
        else None,
        "reference_integrity": None,
        "metric_semantics_verified": False,
        "warm_start_mixer_verified": False,
        "zero_noise_equivalence_verified": False,
        "readout_model_verified": False,
        "operator_equivalence": False,
        "checked_states": 0,
        "resources": None,
        "hardware_used": False,
        "quality_training_performed": False,
        "verification_atol": VERIFICATION_ATOL,
        "errors": [],
    }
    try:
        integrity = verify_reference_integrity(root)
        report["reference_integrity"] = integrity
        if not integrity["unchanged"]:
            raise ValueError("Frozen M1/M2/M3 reference integrity failed")
        report["historical_audits"] = _historical_audits(root, integrity)
        references = load_m4_references(root)
        if not references:
            raise ValueError("The frozen M4 reference suite is empty")
        _check_manifest(root, manifest, config, references, integrity)
        limits = ResourceLimits(
            max_qubits=config.max_qubits,
            max_memory_mb=config.max_memory_mb,
            max_parallel_threads=config.max_parallel_threads,
            max_instances=config.max_instances,
            max_training_runs=config.max_training_runs,
            max_density_runs=config.max_density_runs,
        )
        compiler = CompilerConfig(seed=config.transpiler_seed)
        training_runs = len(references) * len(config.depths) * len(config.restart_seeds) * 2
        report["resources"] = resource_preflight(
            max((reference.qubo.number_variables for reference in references.values()), default=0),
            limits,
            instance_count=len(references),
            training_runs=training_runs,
            density_runs=training_runs * len(DEFAULT_NOISE_PROFILES),
        )
        matrix_gate = _mixer_matrix_gate()
        report["mixer_matrix_gate"] = matrix_gate
        operator_rows: list[dict[str, Any]] = []
        parity_rows: list[dict[str, Any]] = []
        density_rows: list[dict[str, Any]] = []
        maximum_parity = maximum_density = maximum_compile = 0.0
        for name, reference in references.items():
            operator = verify_operator(reference.ising, config.max_qubits)
            if not operator["passed"]:
                raise ValueError(f"Full basis-state operator equivalence failed: {name}")
            plan_table = build_plan_table(reference, max_qubits=config.max_qubits)
            operator_rows.append(
                {
                    "reference_id": name,
                    "reference_hash": reference.reference_hash,
                    **operator,
                    "plan_count": len(plan_table.plans),
                    "plan_semantic_containment_verified": True,
                }
            )
            report["checked_states"] += operator["checked_states"]
            values = np.full(reference.qubo.number_variables, 0.5)
            for p in (1, 2):
                standard = build_qaoa(reference.ising, p, max_qubits=config.max_qubits)
                warm = build_warm_qaoa(
                    reference.ising, p, values, epsilon=0.5, max_qubits=config.max_qubits
                )
                if [parameter.name for parameter in warm.parameters] != [
                    parameter.name for parameter in standard.parameters
                ]:
                    raise ValueError("Standard and warm actual angle-object order disagree")
                angles = np.linspace(0.19, 0.83, len(standard.parameters))
                before = np.asarray(
                    Statevector.from_instruction(bind_parameters(standard, angles)).data
                )
                after = np.asarray(Statevector.from_instruction(bind_parameters(warm, angles)).data)
                difference = _state_difference(before, after)
                maximum_parity = max(maximum_parity, difference)
                parity_rows.append(
                    {
                        "reference_id": name,
                        "p": p,
                        "epsilon": 0.5,
                        "parameter_order": [parameter.name for parameter in standard.parameters],
                        "angles": angles.tolist(),
                        "state_max_difference_up_to_global_phase": difference,
                        "passed": difference <= VERIFICATION_ATOL,
                    }
                )
            for algorithm in ("standard", "warm_start"):
                prepared = (
                    build_qaoa(reference.ising, 1, max_qubits=config.max_qubits)
                    if algorithm == "standard"
                    else build_warm_qaoa(
                        reference.ising, 1, values, epsilon=0.25, max_qubits=config.max_qubits
                    )
                )
                angles = np.linspace(0.19, 0.83, len(prepared.parameters))
                compiled = compile_bound_circuit(
                    bind_parameters(prepared, angles), compiler, limits
                )
                density = simulate_density(compiled, DEFAULT_NOISE_PROFILES[0], limits)
                difference = float(
                    density.physicality["zero_noise_density_max_absolute_difference"]
                )
                maximum_density = max(maximum_density, difference)
                maximum_compile = max(
                    maximum_compile,
                    compiled.metadata["max_amplitude_difference_up_to_global_phase"],
                )
                density_rows.append(
                    {
                        "reference_id": name,
                        "algorithm": algorithm,
                        "p": 1,
                        "warm_y_fixture": values.tolist() if algorithm == "warm_start" else None,
                        "angles": angles.tolist(),
                        "compiler": compiled.metadata,
                        "physicality": density.physicality,
                        "noise_gate_coverage": density.coverage,
                        "density_runtime_ms": density.runtime_ms,
                    }
                )
        channel_gate = _readout_channel_gate(limits, compiler)
        report.update(
            {
                "operator_rows": operator_rows,
                "operator_equivalence": all(row["passed"] for row in operator_rows),
                "metric_semantics_verified": True,
                "warm_standard_parity_rows": parity_rows,
                "warm_start_mixer_verified": matrix_gate["passed"]
                and all(row["passed"] for row in parity_rows),
                "zero_noise_rows": density_rows,
                "zero_noise_equivalence_verified": all(
                    row["physicality"]["zero_noise_statevector_equivalence_verified"]
                    for row in density_rows
                ),
                "readout_channel_gate": channel_gate,
                "readout_model_verified": channel_gate["passed"],
                "max_warm_standard_amplitude_difference": maximum_parity,
                "max_zero_noise_density_difference": maximum_density,
                "max_pre_post_compilation_amplitude_difference": maximum_compile,
                "reference_count": len(references),
                "zero_noise_circuit_count": len(density_rows),
            }
        )
        if all(
            report[field]
            for field in (
                "operator_equivalence",
                "metric_semantics_verified",
                "warm_start_mixer_verified",
                "zero_noise_equivalence_verified",
                "readout_model_verified",
            )
        ):
            report["status"] = "PASS"
        else:
            report["errors"].append("An executed M4 technical gate failed; see gate evidence")
    except (ValueError, OSError, KeyError, TypeError) as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    report["duration_seconds"] = perf_counter() - started
    # Return the same JSON-domain structure that is persisted (tuples become lists).
    report = json.loads(json.dumps(report, allow_nan=False))
    write_json(output / "verification.json", report)
    return report
