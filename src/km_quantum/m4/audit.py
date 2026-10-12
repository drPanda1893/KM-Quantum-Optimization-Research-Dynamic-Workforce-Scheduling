"""Portable artifact seals and independent audits of saved M4 observations.

The audit rebinds ideal circuits, reconstructs classical controls and seeded
counts, and checks exact readout application. It does not optimize angles or
re-run primary noisy simulations. Seals record channel distributions at their
original execution; they are integrity records, not digital signatures.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from qiskit.quantum_info import Statevector
from threadpoolctl import threadpool_limits

from km_quantum.m4.io import canonical_hash, read_json, verify_reference_integrity, write_json
from km_quantum.m4.manifest import resource_limits, source_content_hash
from km_quantum.m4.metrics import analyze_distribution, build_plan_table, sample_metrics
from km_quantum.m4.models import M4Config
from km_quantum.m4.noise import (
    PHYSICALITY_ATOL,
    CompilerConfig,
    NoiseProfile,
    compile_bound_circuit,
    gate_noise_model,
    multinomial_counts,
    noise_gate_coverage,
    symmetric_readout_matrices,
)
from km_quantum.m4.relaxation import RelaxationConfig, build_box_relaxation
from km_quantum.m4.suite import load_m4_references
from km_quantum.m4.training import _preparation_quality_gate
from km_quantum.m4.warm_start import build_warm_qaoa, round_relaxation, warm_product_probabilities
from km_quantum.qaoa.benchmark import _assert_record_matches
from km_quantum.qaoa.ordering import m2_bits_to_basis_index
from km_quantum.qaoa.simulation import bind_parameters, build_qaoa
from km_quantum.qubo.models import Polynomial

INDEX_NAME = "artifact_index.jsonl"


def _relative_path(relativepath: str | Path) -> str:
    path = Path(relativepath)
    if (
        path.drive
        or path.root
        or path.is_absolute()
        or not path.parts
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ValueError("Artifact seal paths must stay relative to the M4 output directory")
    return path.as_posix()


def read_seals(output: Path) -> dict[str, str]:
    """Read the append-only index; duplicates or malformed seals are invalid."""
    path = output / INDEX_NAME
    if not path.exists():
        return {}
    seals: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            raise ValueError("Artifact index contains an empty record")
        entry = json.loads(line)
        relative = _relative_path(entry["artifact_path"])
        digest = entry["canonical_sha256"]
        if entry.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Artifact index contains an invalid seal")
        if relative in seals:
            raise ValueError("Artifact index contains a duplicate path")
        seals[relative] = digest
    return seals


def append_seal(output: Path, relativepath: str | Path, row: Any) -> dict[str, Any]:
    """Record a JSON-domain content hash once; do not silently replace a seal."""
    relative = _relative_path(relativepath)
    entry = {
        "schema_version": 1,
        "artifact_path": relative,
        "canonical_sha256": canonical_hash(row),
    }
    seals = read_seals(output)
    if relative in seals:
        if seals[relative] != entry["canonical_sha256"]:
            raise ValueError(f"Refusing to replace an existing artifact seal: {relative}")
        return entry
    output.mkdir(parents=True, exist_ok=True)
    with (output / INDEX_NAME).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
    return entry


def verify_seal(output: Path, relativepath: str | Path, row: Any) -> None:
    relative = _relative_path(relativepath)
    if read_seals(output).get(relative) != canonical_hash(row):
        raise ValueError(f"Saved artifact does not match its execution seal: {relative}")


def _validate_preparation(row: dict[str, Any], reference: Any, config: M4Config) -> None:
    saved = row["relaxation"]
    polynomial = Polynomial(
        number_variables=reference.qubo.number_variables,
        constant=reference.qubo.constant,
        linear=reference.qubo.linear,
        quadratic=reference.qubo.quadratic,
    )
    relaxation = build_box_relaxation(polynomial, max_variables=config.max_qubits)
    expected_config = asdict(
        RelaxationConfig(
            max_function_evaluations=config.relaxation_max_evaluations,
            max_iterations=config.relaxation_max_iterations,
            ftol=config.relaxation_ftol,
            gtol=config.relaxation_gtol,
            max_variables=config.max_qubits,
        )
    )
    _assert_record_matches(expected_config, saved["config"], "relaxation.config")
    for key, value in (
        ("lambda", relaxation.lambda_value),
        ("numerical_scale", relaxation.numerical_scale),
        ("minimum_eigenvalue", relaxation.minimum_eigenvalue),
        ("exact_row_bound", str(relaxation.exact_row_bound)),
    ):
        _assert_record_matches(value, saved[key], f"relaxation.{key}")
    value, _ = relaxation.value_and_gradient(saved["y"])
    diagnostics = relaxation.diagnostics(saved["y"])
    _assert_record_matches(value, saved["objective_value"], "relaxation.objective_value")
    _assert_record_matches(
        value / relaxation.numerical_scale,
        saved["scaled_objective_value"],
        "relaxation.scaled_objective_value",
    )
    for key, actual in diagnostics.items():
        _assert_record_matches(actual, saved[key], f"relaxation.{key}")
    _preparation_quality_gate(saved, config, polynomial.number_variables)
    stationary = diagnostics["scaled_projected_gradient_inf"] <= config.relaxation_gtol
    if saved["numerically_stationary"] is not stationary:
        raise ValueError("Saved strict stationarity flag disagrees with actual diagnostics")
    if saved["initial_y"] != [0.5] * polynomial.number_variables:
        raise ValueError("Relaxation initialization differs from the frozen all-half vector")
    trace = saved["trace"]
    nfev = saved["function_evaluations"]
    if (
        type(nfev) is not int
        or nfev != saved["gradient_evaluations"]
        or nfev != len(trace)
        or not 0 <= nfev <= config.relaxation_max_evaluations
        or [item["evaluation"] for item in trace] != list(range(1, nfev + 1))
    ):
        raise ValueError("Relaxation trace/evaluation budget accounting failed")
    for item in trace:
        trace_value, _ = relaxation.value_and_gradient(item["y"], scaled=True)
        _assert_record_matches(trace_value, item["scaled_value"], "relaxation.trace.value")
    if trace:
        best = min(trace, key=lambda item: item["scaled_value"])
        _assert_record_matches(best["y"], saved["y"], "relaxation.best_y")
    if (
        saved["iterations"] is None
        or type(saved["iterations"]) is not int
        or not 0 <= saved["iterations"] <= config.relaxation_max_iterations
    ):
        raise ValueError("Relaxation iteration budget failed")


def _validate_trace(
    row: dict[str, Any], output: Path, reference: Any, table: Any, config: M4Config
) -> None:
    trace = [
        json.loads(line)
        for line in (output / "traces" / f"{row['training_id']}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    if any(item["phase"] != "optimizer" for item in trace[:-1]):
        raise ValueError("Optimizer trace contains an unexpected verification phase")
    if (
        row["verification_evaluations"] != 1
        or row["total_expectation_evaluations"] != len(trace)
        or row["nfev"] != len(trace) - 1
    ):
        raise ValueError("Training expectation/verification accounting failed")
    prepared = (
        build_qaoa(reference.ising, row["p"], max_qubits=config.max_qubits)
        if row["algorithm"] == "standard"
        else build_warm_qaoa(
            reference.ising,
            row["p"],
            row["relaxation"]["y"],
            epsilon=config.epsilon,
            max_qubits=config.max_qubits,
        )
    )
    initial = Statevector.from_instruction(
        bind_parameters(prepared, row["initial_parameters"])
    ).probabilities()
    initial_energy = float(np.dot(initial, table.state_table.energies))
    _assert_record_matches(
        initial_energy, row["initial_expected_energy"], "training.initial_energy"
    )
    _assert_record_matches(
        initial_energy, trace[0]["expected_energy"], "training.initial_trace_energy"
    )
    if row["energy_offset"] != prepared.operator.energy_offset:
        raise ValueError("Training saved scalar offset differs from the frozen Hamiltonian")
    previous_elapsed = 0.0
    for item in trace:
        if (
            not np.isfinite(
                [
                    item["expectation"],
                    item["expected_energy"],
                    item["elapsed_ms"],
                    item["evaluation_runtime_ms"],
                ]
            ).all()
            or item["elapsed_ms"] < previous_elapsed
            or item["evaluation_runtime_ms"] < 0
        ):
            raise ValueError("Training trace has invalid energy/timing evidence")
        _assert_record_matches(
            item["expectation"] + row["energy_offset"],
            item["expected_energy"],
            "training.trace.offset",
        )
        previous_elapsed = item["elapsed_ms"]
    expected_optimizer = {
        "maxiter": config.optimizer_maxiter,
        "tolerance": config.optimizer_tolerance,
        "rhobeg": config.optimizer_rhobeg,
    }
    _assert_record_matches(expected_optimizer, row["optimizer_config"], "training.optimizer_config")
    if row.get("cost_evolution") != "exp(-i*gamma*H_cost)" or row.get("mixer_sign") != 1:
        raise ValueError("Training sign convention differs from frozen M3")
    if row.get("mixer_evolution") != "exp(-i*beta*H_mixer)":
        raise ValueError("Training mixer evolution convention differs")
    if row.get("hamiltonian_scale") != "unchanged frozen M2 coefficients":
        raise ValueError("Training changed the frozen Hamiltonian scale")


def _compiler_without_timing(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if key != "compile_runtime_ms"}


def _validate_noise_metadata(row: dict[str, Any], compiled: Any, config: M4Config) -> None:
    profile = NoiseProfile(**row["noise_profile"])
    metadata = row["noise_metadata"]
    _assert_record_matches(asdict(profile), metadata["noise_profile"], "noise.profile")
    coverage = noise_gate_coverage(compiled.circuit, profile, gate_noise_model(profile))
    _assert_record_matches(coverage, metadata["noise_gate_coverage"], "noise.gate_coverage")
    matrices = symmetric_readout_matrices(compiled.circuit.num_qubits, profile.readout_flip)
    _assert_record_matches(
        [matrix.tolist() for matrix in matrices],
        metadata["readout_matrices_qubit_order"],
        "noise.readout_matrices",
    )
    physicality = metadata["density_physicality"]
    dimension = 1 << compiled.circuit.num_qubits
    if (
        physicality.get("verified") is not True
        or physicality["dimension"] != dimension
        or abs(complex(physicality["trace_real"], physicality["trace_imaginary"]) - 1)
        > PHYSICALITY_ATOL
        or not 0 <= physicality["hermiticity_max_absolute_error"] <= PHYSICALITY_ATOL
        or physicality["minimum_eigenvalue"] < -PHYSICALITY_ATOL
        or not 0 <= physicality["born_probability_max_roundoff_adjustment"] <= PHYSICALITY_ATOL
        or physicality["readout_applied"] is not False
    ):
        raise ValueError("Saved density physicality evidence is invalid")
    if not all(np.isfinite(value) for value in physicality.values() if isinstance(value, float)):
        raise ValueError("Saved density physicality evidence is nonfinite")
    if not profile.lambda_1q and not profile.lambda_2q:
        _assert_record_matches(
            row["probabilities"]["ideal"],
            row["probabilities"]["before_readout"],
            "noise.zero_gate_error_probabilities",
        )
        if physicality["zero_noise_statevector_equivalence_verified"] is not True:
            raise ValueError("Zero-noise density equivalence evidence is absent")
        if physicality["zero_noise_density_max_absolute_difference"] > PHYSICALITY_ATOL:
            raise ValueError("Zero-noise density discrepancy exceeds tolerance")
    for layer, field in (
        ("before_readout", "total_variation_distance_ideal_to_before_readout"),
        ("after_readout", "total_variation_distance_ideal_to_observed"),
    ):
        tv = (
            0.5
            * np.abs(
                np.asarray(row["probabilities"]["ideal"]) - np.asarray(row["probabilities"][layer])
            ).sum()
        )
        _assert_record_matches(float(tv), metadata[field], f"noise.{field}")
    backend = metadata["density_backend"]
    expected_options = {
        "method": "density_matrix",
        "device": "CPU",
        "precision": "double",
        "max_memory_mb": config.max_memory_mb,
        "max_parallel_threads": config.max_parallel_threads,
        "max_parallel_experiments": 1,
        "max_parallel_shots": 1,
        "enable_truncation": False,
        "zero_threshold": 1e-14,
        "fusion_enable": False,
    }
    _assert_record_matches(expected_options, backend["options"], "density.backend_options")
    if backend.get("seed_simulator") != 0 or backend.get("snapshot_execution_shots") != 1:
        raise ValueError("Density snapshot execution seed/shots differ from the frozen policy")
    if (
        backend["measurement_present"] is not False
        or backend["readout_present"] is not False
        or backend["sequence_sha256"] != compiled.sequence_hash
    ):
        raise ValueError(
            "Density backend incorrectly includes measurement/readout or a changed circuit"
        )


def _validate_noise_timings(
    row: dict[str, Any], training: dict[str, Any], relaxation: dict[str, Any], config: M4Config
) -> None:
    for key, value in row["training"].items():
        _assert_record_matches(training[key], value, f"noise.training_summary.{key}")
    timings = row["timings"]
    preparation_ms = relaxation["runtime_ms"] if row["algorithm"] == "warm_start" else 0.0
    components = {
        "ideal_training_runtime_ms": training["runtime_ms"],
        "relaxation_runtime_ms": preparation_ms,
        "compile_runtime_ms": row["compiler"]["compile_runtime_ms"],
        "density_runtime_ms": row["noise_metadata"]["density_runtime_ms"],
        "readout_runtime_ms": row["noise_metadata"]["readout_runtime_ms"],
    }
    for key, expected in components.items():
        _assert_record_matches(expected, timings[key], f"noise.timings.{key}")
    sample_ms = timings["sampling_analysis_runtime_ms"]
    if not np.isfinite(sample_ms) or sample_ms < 0:
        raise ValueError("Saved sampling analysis runtime is invalid")
    distribution_ms = timings.get("distribution_analysis_runtime_ms", 0.0)
    if not np.isfinite(distribution_ms) or distribution_ms < 0:
        raise ValueError("Saved exact distribution analysis runtime is invalid")
    base_ms = sum(value for key, value in components.items() if key != "relaxation_runtime_ms")
    base_ms += sample_ms + distribution_ms
    _assert_record_matches(
        base_ms + preparation_ms, timings["standalone_end_to_end_ms"], "noise.timings.standalone"
    )
    amortized = base_ms + preparation_ms / (len(config.depths) * len(config.restart_seeds))
    _assert_record_matches(amortized, timings["amortized_end_to_end_ms"], "noise.timings.amortized")


def _validate_aggregate_rows(output: Path, filename: str, folder: str) -> None:
    path = output / filename
    if not path.exists():
        raise ValueError(f"Saved aggregate artifact is missing: {filename}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    expected = [read_json(record) for record in sorted((output / folder).glob("*.json"))]
    if canonical_hash(rows) != canonical_hash(expected):
        raise ValueError(f"Saved aggregate artifact differs from original records: {filename}")


def _audit_execution_ledger(
    output: Path,
    plan: dict[str, Any],
    context: dict[str, Any],
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Require one honest status for every frozen noise execution, including skips."""
    planned = plan["noise"]
    expected_ids = [row["run_id"] for row in planned]
    completed_ids = {row["run_id"] for row in records if row.get("status") == "COMPLETE"}
    failed_ids = {row["failed_execution_id"] for row in failures}
    failed_training = {row["training_id"] for row in failures}
    missing_files = []
    execution_path = output / "benchmark_execution.json"
    execution = read_json(execution_path) if execution_path.exists() else None
    stopped = any(row.get("error_type") == "TimeoutError" for row in failures)
    if execution is None:
        missing_files.append("benchmark_execution.json")
    else:
        for key, value in (
            ("scope", plan["scope"]),
            ("plan_hash", plan["plan_hash"]),
            ("training", len(plan["training"])),
            ("density", len(planned)),
            ("controls", len(plan["controls"])),
            ("preparations", len(plan["reference_ids"])),
            ("completed_executions", len(records)),
            ("failures", failures),
            ("stopped_at_wall_limit", stopped),
            ("code_sha", context["environment"]["git_commit"]),
            ("config_hash", manifest["config_hash"]),
            ("manifest_content_sha256", manifest["manifest_content_sha256"]),
            (
                "status",
                "COMPLETE" if len(records) == len(planned) and not failures else "INCOMPLETE",
            ),
        ):
            _assert_record_matches(value, execution.get(key), f"benchmark_execution.{key}")
        if not np.isfinite(execution["duration_seconds"]) or execution["duration_seconds"] < 0:
            raise ValueError("Saved benchmark execution duration is invalid")
    if any(identifier is not None and identifier not in expected_ids for identifier in failed_ids):
        raise ValueError("Failure evidence references an execution outside the frozen ledger")
    training_ids = {row["training_id"] for row in plan["training"]}
    if failed_training - training_ids - {"campaign_abort"}:
        raise ValueError("Failure evidence references training outside the frozen plan")
    path = output / "execution_ledger.jsonl"
    if not path.exists():
        missing_files.append("execution_ledger.jsonl")
        return (
            {
                "status": "INCOMPLETE",
                "planned": len(planned),
                "ledger_rows": 0,
                "counts": None,
                "missing_files": missing_files,
            },
            [],
            missing_files,
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError("Execution ledger contains an empty record")
    rows = [json.loads(line) for line in lines]
    if [row.get("run_id") for row in rows] != expected_ids:
        raise ValueError("Execution ledger must contain every planned ID once in frozen order")
    skipped = []
    for expected, row in zip(planned, rows, strict=True):
        identifier = expected["run_id"]
        status = (
            "COMPLETE"
            if identifier in completed_ids
            else "FAILED"
            if identifier in failed_ids
            else "SKIPPED_WALL_LIMIT"
            if stopped
            else "SKIPPED_DEPENDENCY_FAILURE"
            if expected["training_id"] in failed_training
            else "MISSING"
        )
        if canonical_hash({**expected, "status": status}) != canonical_hash(row):
            raise ValueError(f"Execution ledger identity/status differs: {identifier}")
        if status.startswith("SKIPPED_"):
            skipped.append(row)
    statuses = Counter(row["status"] for row in rows)
    counts = {
        "planned": len(planned),
        "completed": statuses["COMPLETE"],
        "failed": statuses["FAILED"],
        "skipped": len(skipped),
        "missing": statuses["MISSING"],
        "attempted_or_completed": statuses["COMPLETE"] + statuses["FAILED"],
        "statuses": dict(sorted(statuses.items())),
    }
    return (
        {
            "status": "INCOMPLETE" if missing_files else "PASS",
            "planned": len(planned),
            "ledger_rows": len(rows),
            "counts": counts,
            "missing_files": missing_files,
            "all_planned_statuses_visible": True,
        },
        skipped,
        missing_files,
    )


def audit_experiment(
    root: Path, output: Path, manifest: dict[str, Any], config: M4Config
) -> dict[str, Any]:
    """Audit exactly the frozen primary plan; preserve missing/failed observations."""
    # Import after module initialization so benchmark can use seal helpers safely.
    from km_quantum.m4.benchmark import (
        _load_training,
        _validate_identity,
        _validate_saved_noise,
        execution_plan,
    )

    started = perf_counter()
    report: dict[str, Any] = {
        "status": "INVALID",
        "errors": [],
        "missing": [],
        "failed": [],
        "skipped": [],
        "execution_ledger": {"status": "NOT_CHECKED"},
        "unexpected": [],
        "checked": {"preparations": 0, "controls": 0, "training": 0, "compiled": 0, "density": 0},
        "config_hash": manifest["config_hash"],
        "manifest_content_sha256": manifest["manifest_content_sha256"],
        "hardware_used": False,
        "optimization_performed": False,
        "primary_density_resimulation_performed": False,
        "sampling_is_seeded_count_regeneration": True,
    }
    try:
        integrity = verify_reference_integrity(root)
        report["reference_integrity"] = integrity
        if not integrity["unchanged"]:
            raise ValueError("Historical reference integrity failed during saved-result audit")
        context = read_json(output / "execution_context.json")
        plan = execution_plan(manifest, context["scope"] == "technical_smoke")
        report["scope"] = context["scope"]
        if context["scope"] not in {"primary", "technical_smoke"}:
            raise ValueError("Unknown experiment execution scope")
        if (
            context["plan_hash"] != plan["plan_hash"]
            or context["manifest_content_sha256"] != manifest["manifest_content_sha256"]
        ):
            raise ValueError("Saved execution context does not match the frozen plan")
        if context["source_content_hash"] != source_content_hash(root):
            raise ValueError("Saved execution source hash differs from the current experiment code")
        sha = context["environment"]["git_commit"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Saved code SHA is not a full Git commit")
        if context["scope"] == "primary" and context["environment"].get("git_dirty") is not False:
            raise ValueError("Primary execution was not recorded from a clean commit")
        references = load_m4_references(root)
        entries = {row["reference_id"]: row for row in manifest["references"]}
        expected_paths = {
            *(f"preparations/{name}.json" for name in plan["reference_ids"]),
            *(f"controls/{row['control_id']}.json" for row in plan["controls"]),
            *(f"training/{row['training_id']}.json" for row in plan["training"]),
            *(f"compiled/{row['training_id']}.json" for row in plan["training"]),
            *(f"records/{row['run_id']}.json" for row in plan["noise"]),
        }
        seals = read_seals(output)
        actual_paths = {
            path.relative_to(output).as_posix()
            for folder in ("preparations", "controls", "training", "compiled", "records")
            for path in (output / folder).glob("*.json")
        }
        report["missing"] = sorted(expected_paths - actual_paths)
        expected_traces = {f"traces/{row['training_id']}.jsonl" for row in plan["training"]}
        actual_traces = {
            path.relative_to(output).as_posix() for path in (output / "traces").glob("*.jsonl")
        }
        report["missing"] += sorted(expected_traces - actual_traces)
        report["unexpected"] = sorted(actual_paths - expected_paths)
        report["unexpected"] += sorted(actual_traces - expected_traces)
        report["failed"] = [
            read_json(path) for path in sorted((output / "failures").glob("*.json"))
        ]
        if report["unexpected"]:
            raise ValueError("Saved artifacts include IDs outside the frozen execution plan")
        for relative in actual_paths:
            if seals.get(relative) != canonical_hash(read_json(output / relative)):
                raise ValueError(f"Saved artifact execution seal mismatch: {relative}")
        if set(seals) != actual_paths:
            raise ValueError("Artifact index paths differ from the present execution artifacts")
        records = [read_json(path) for path in sorted((output / "records").glob("*.json"))]
        ledger, skipped, missing_ledger_files = _audit_execution_ledger(
            output, plan, context, manifest, records, report["failed"]
        )
        report["execution_ledger"] = ledger
        report["skipped"] = skipped
        report["missing"] += missing_ledger_files
        tables = {
            name: build_plan_table(references[name], config.max_qubits)
            for name in plan["reference_ids"]
        }
        preparations: dict[str, dict[str, Any]] = {}
        with threadpool_limits(limits=config.max_parallel_threads):
            for name in plan["reference_ids"]:
                path = output / "preparations" / f"{name}.json"
                if not path.exists():
                    continue
                row = read_json(path)
                _validate_identity(row, references[name], entries[name], manifest, context)
                _validate_preparation(row, references[name], config)
                preparations[name] = row["relaxation"]
                report["checked"]["preparations"] += 1
            for expected in plan["controls"]:
                name = expected["reference_id"]
                path = output / "controls" / f"{expected['control_id']}.json"
                if not path.exists() or name not in preparations:
                    continue
                row = read_json(path)
                reference, table = references[name], tables[name]
                _validate_identity(row, reference, entries[name], manifest, context)
                for key, expected_value in expected.items():
                    _assert_record_matches(expected_value, row.get(key), f"control.{key}")
                _assert_record_matches(preparations[name], row["relaxation"], "control.relaxation")
                values = (
                    np.full(
                        1 << reference.qubo.number_variables,
                        1 / (1 << reference.qubo.number_variables),
                    )
                    if expected["control"] == "uniform"
                    else warm_product_probabilities(preparations[name]["y"], epsilon=config.epsilon)
                )
                if expected["control"] == "rounded_relaxation":
                    values = np.zeros(1 << reference.qubo.number_variables)
                    values[m2_bits_to_basis_index(round_relaxation(preparations[name]["y"]))] = 1
                _assert_record_matches(
                    values.tolist(), row["probabilities"], "control.probabilities"
                )
                _assert_record_matches(
                    analyze_distribution(reference, table, values),
                    row["metrics"],
                    "control.metrics",
                )
                counts = multinomial_counts(
                    values, shots=config.shots, seed=expected["sampling_seed"]
                )
                _assert_record_matches(counts, row["sampling"]["counts"], "control.seeded_counts")
                _assert_record_matches(
                    sample_metrics(
                        reference,
                        table,
                        counts,
                        shots=config.shots,
                        seed=expected["sampling_seed"],
                        probabilities=values,
                    ),
                    row["sampling"],
                    "control.sampling",
                )
                _assert_record_matches(
                    entries[name]["cp_sat_reference"],
                    row["cp_sat_reference"],
                    "control.cp_sat_reference",
                )
                if row["classical_distribution_only"] is not True:
                    raise ValueError("Control is not marked as a classical distribution")
                report["checked"]["controls"] += 1
            for expected in plan["training"]:
                name, training_id = expected["reference_id"], expected["training_id"]
                path = output / "training" / f"{training_id}.json"
                if not path.exists() or name not in preparations:
                    continue
                reference, table = references[name], tables[name]
                training, bound, _ = _load_training(
                    path,
                    expected,
                    reference,
                    table,
                    entries[name],
                    config,
                    manifest,
                    context,
                    output,
                )
                _validate_trace(training, output, reference, table, config)
                if expected["algorithm"] == "warm_start":
                    _assert_record_matches(
                        preparations[name], training["relaxation"], "training.relaxation"
                    )
                report["checked"]["training"] += 1
                compiled = compile_bound_circuit(
                    bound, CompilerConfig(seed=config.transpiler_seed), resource_limits(config)
                )
                compiled_path = output / "compiled" / f"{training_id}.json"
                if not compiled_path.exists():
                    continue
                saved_compiler = read_json(compiled_path)
                _assert_record_matches(
                    _compiler_without_timing(compiled.metadata),
                    _compiler_without_timing(saved_compiler),
                    "compiled.metadata",
                )
                report["checked"]["compiled"] += 1
                for noise_expected in [
                    item for item in plan["noise"] if item["training_id"] == training_id
                ]:
                    noise_path = output / "records" / f"{noise_expected['run_id']}.json"
                    if not noise_path.exists():
                        continue
                    row = read_json(noise_path)
                    _validate_saved_noise(
                        row,
                        noise_expected,
                        training,
                        reference,
                        table,
                        entries[name],
                        compiled,
                        config,
                        manifest,
                        context,
                    )
                    expected_compiler = {
                        key: value
                        for key, value in saved_compiler.items()
                        if key != "gate_sequence"
                    }
                    _assert_record_matches(
                        expected_compiler, row["compiler"], "noise.compiled_metadata"
                    )
                    _validate_noise_metadata(row, compiled, config)
                    _validate_noise_timings(row, training, preparations[name], config)
                    _assert_record_matches(
                        preparations[name], row["relaxation"], "noise.relaxation"
                    )
                    counts = multinomial_counts(
                        row["probabilities"]["after_readout"],
                        shots=config.shots,
                        seed=noise_expected["sampling_seed"],
                    )
                    _assert_record_matches(counts, row["sampling"]["counts"], "noise.seeded_counts")
                    if set(row["probabilities"]) != {"ideal", "before_readout", "after_readout"}:
                        raise ValueError(
                            "Noise record does not separate exactly three distribution layers"
                        )
                    if (
                        row.get("hardware_used") is not False
                        or row.get("training_noise_used") is not False
                    ):
                        raise ValueError(
                            "Primary M4 records incorrectly claim hardware or noisy training"
                        )
                    report["checked"]["density"] += 1
        _validate_aggregate_rows(output, "runs.jsonl", "records")
        _validate_aggregate_rows(output, "controls.jsonl", "controls")
        report["status"] = "INCOMPLETE" if report["missing"] or report["failed"] else "PASS"
    except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    report["duration_seconds"] = perf_counter() - started
    report = json.loads(json.dumps(report, allow_nan=False))
    write_json(output / "audit.json", report)
    return report
