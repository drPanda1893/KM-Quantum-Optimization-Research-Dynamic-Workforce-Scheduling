"""Bounded ideal training followed by frozen-parameter channel transfer."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from qiskit.quantum_info import Statevector
from threadpoolctl import threadpool_limits

from km_quantum.m4.audit import append_seal, verify_seal
from km_quantum.m4.io import (
    canonical_hash,
    execution_environment,
    lf_file_hash,
    read_json,
    write_json,
)
from km_quantum.m4.manifest import (
    load_config,
    resource_limits,
    source_content_hash,
    validate_manifest,
)
from km_quantum.m4.metrics import analyze_distribution, build_plan_table, sample_metrics
from km_quantum.m4.models import M4Config
from km_quantum.m4.noise import (
    CompilerConfig,
    NoiseProfile,
    compile_bound_circuit,
    evaluate_noise,
    multinomial_counts,
)
from km_quantum.m4.relaxation import RelaxationConfig, solve_box_relaxation
from km_quantum.m4.suite import load_m4_references
from km_quantum.m4.training import train_variant
from km_quantum.m4.warm_start import build_warm_qaoa, round_relaxation, warm_product_probabilities
from km_quantum.qaoa.benchmark import _assert_record_matches
from km_quantum.qaoa.ordering import m2_bits_to_basis_index
from km_quantum.qaoa.simulation import bind_parameters, build_qaoa
from km_quantum.qubo.models import Polynomial


def output_directory(root: Path, output: str | Path) -> Path:
    path = Path(output)
    path = (root / path if not path.is_absolute() else path).resolve()
    result_root = (root / "results").resolve()
    if (
        not path.is_relative_to(result_root)
        or not path.relative_to(result_root).parts
        or not path.relative_to(result_root).parts[0].startswith("m4")
    ):
        raise ValueError("M4 outputs must use a dedicated results/m4* directory")
    return path


def execution_plan(manifest: dict[str, Any], smoke: bool = False) -> dict[str, Any]:
    training = manifest["planned_training_runs"]
    controls = manifest["planned_control_runs"]
    noise = manifest["planned_noise_runs"]
    if smoke:
        training = [
            row
            for row in training
            if row["reference_id"] == "A_single_job"
            and row["p"] == 1
            and row["initialization_seed"] == 0
        ]
        ids = {row["training_id"] for row in training}
        noise = [row for row in noise if row["training_id"] in ids]
        controls = [row for row in controls if row["reference_id"] == "A_single_job"]
    ids = {row["reference_id"] for row in training}
    result = {
        "scope": "technical_smoke" if smoke else "primary",
        "training": training,
        "noise": noise,
        "controls": controls,
        "reference_ids": sorted(ids),
    }
    result["plan_hash"] = canonical_hash(result)
    return result


def _identity(
    reference: Any, entry: dict[str, Any], manifest: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "reference_id": reference.name,
        "reference_hash": reference.reference_hash,
        "qubo_hash": reference.qubo_hash,
        "ising_hash": reference.ising_hash,
        "group": entry["group"],
        "split": entry["split"],
        "qubit_count": reference.qubo.number_variables,
        "config_hash": manifest["config_hash"],
        "manifest_content_sha256": manifest["manifest_content_sha256"],
        "code_sha": context["environment"]["git_commit"],
        "environment": context["environment"],
        "source_content_hash": context["source_content_hash"],
        "scope": context["scope"],
    }


def _validate_identity(
    row: dict[str, Any],
    reference: Any,
    entry: dict[str, Any],
    manifest: dict[str, Any],
    context: dict[str, Any],
) -> None:
    if row.get("status") != "COMPLETE":
        raise ValueError(
            "Incomplete or failed records remain visible and cannot be silently replaced"
        )
    for key, value in _identity(reference, entry, manifest, context).items():
        _assert_record_matches(value, row.get(key), f"identity.{key}")


def _load_training(
    path: Path,
    expected: dict[str, Any],
    reference: Any,
    table: Any,
    entry: dict[str, Any],
    config: M4Config,
    manifest: dict[str, Any],
    context: dict[str, Any],
    output: Path,
) -> tuple[dict[str, Any], Any, np.ndarray]:
    row = read_json(path)
    _validate_identity(row, reference, entry, manifest, context)
    for key in ("algorithm", "p", "initialization_seed"):
        if row[key] != expected[key]:
            raise ValueError("Saved training identity differs from the frozen plan")
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
    if row["parameter_order"] != [p.name for p in prepared.parameters]:
        raise ValueError("Saved actual parameter objects differ")
    initial = np.random.default_rng(row["initialization_seed"]).uniform(
        0, np.pi, len(prepared.parameters)
    )
    if initial.tolist() != row["initial_parameters"]:
        raise ValueError("Saved initialization does not match the fixed seed")
    bound = bind_parameters(prepared, row["final_parameters"])
    probabilities = np.asarray(Statevector.from_instruction(bound).probabilities(), dtype=float)
    _assert_record_matches(
        analyze_distribution(reference, table, probabilities),
        row["ideal_metrics"],
        "training.ideal_metrics",
    )
    _assert_record_matches(
        probabilities.tolist(), row["ideal_probabilities"], "training.ideal_probabilities"
    )
    _assert_record_matches(
        float(np.dot(probabilities, table.state_table.energies)),
        row["final_expected_energy"],
        "training.energy",
    )
    trace = output / "traces" / f"{expected['training_id']}.jsonl"
    if lf_file_hash(trace) != row["trace_lf_sha256"]:
        raise ValueError("Saved training trace changed")
    evaluations = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    if (
        len(evaluations) != row["nfev"] + 1
        or row["nfev"] > config.optimizer_maxiter
        or [r["evaluation"] for r in evaluations] != list(range(1, len(evaluations) + 1))
        or evaluations[-1]["phase"] != "verification"
        or evaluations[-1]["parameters"] != row["final_parameters"]
        or evaluations[0]["parameters"] != row["initial_parameters"]
    ):
        raise ValueError("Saved trace is not a complete optimizer trajectory")
    for evaluation in evaluations:
        if (
            len(evaluation["parameters"]) != len(prepared.parameters)
            or not np.isfinite(evaluation["parameters"]).all()
        ):
            raise ValueError("Saved trace has invalid parameter values")
    _assert_record_matches(
        evaluations[-1]["expected_energy"], row["final_expected_energy"], "final trace energy"
    )
    return row, bound, probabilities


def _validate_saved_noise(
    row: dict[str, Any],
    expected: dict[str, Any],
    training: dict[str, Any],
    reference: Any,
    table: Any,
    entry: dict[str, Any],
    compiled: Any,
    config: M4Config,
    manifest: dict[str, Any],
    context: dict[str, Any],
) -> None:
    _validate_identity(row, reference, entry, manifest, context)
    for key in (
        "run_id",
        "training_id",
        "algorithm",
        "p",
        "initialization_seed",
        "noise_profile",
        "sampling_seed",
    ):
        _assert_record_matches(expected[key], row.get(key), key)
    if row["compiler"]["sequence_sha256"] != compiled.sequence_hash:
        raise ValueError("Saved compiled circuit differs")
    _assert_record_matches(
        training["final_parameters"], row["final_parameters"], "transferred angles"
    )
    for layer, values in row["probabilities"].items():
        _assert_record_matches(
            analyze_distribution(reference, table, values),
            row["metrics"][layer],
            f"{layer} metrics",
        )
    _assert_record_matches(
        training["ideal_probabilities"], row["probabilities"]["ideal"], "ideal distribution"
    )
    from km_quantum.m4.noise import apply_readout, symmetric_readout_matrices

    observed = apply_readout(
        row["probabilities"]["before_readout"],
        symmetric_readout_matrices(
            reference.qubo.number_variables, row["noise_profile"]["readout_flip"]
        ),
    )
    _assert_record_matches(
        observed.tolist(), row["probabilities"]["after_readout"], "readout distribution"
    )
    sampled = sample_metrics(
        reference,
        table,
        row["sampling"]["counts"],
        shots=config.shots,
        seed=expected["sampling_seed"],
        probabilities=observed,
    )
    _assert_record_matches(sampled, row["sampling"], "sampling evidence")
    if not sampled["sampling_verified"] or row["noise_metadata"]["readout_applications"] != 1:
        raise ValueError("Saved sampling/readout checks failed")


def _record_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def load_records(output: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        [read_json(p) for p in sorted((output / "records").glob("*.json"))],
        [read_json(p) for p in sorted((output / "controls").glob("*.json"))],
    )


def run_benchmark(
    root: Path,
    output: str | Path,
    *,
    resume: bool = False,
    dry_run: bool = False,
    smoke: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    invocation_started = perf_counter()
    root = root.resolve()
    output = output_directory(root, output)
    config = load_config(root)
    manifest = validate_manifest(root, config)
    plan = execution_plan(manifest, smoke)
    preview = {
        "scope": plan["scope"],
        "training": len(plan["training"]),
        "density": len(plan["noise"]),
        "controls": len(plan["controls"]),
        "preparations": len(plan["reference_ids"]),
        "resources": manifest["initial_resource_preflight"],
        "plan_hash": plan["plan_hash"],
    }
    if dry_run:
        return {"status": "DRY_RUN", **preview}
    output.mkdir(parents=True, exist_ok=True)
    environment = execution_environment(root)
    if not smoke and (not environment["git_commit"] or environment["git_dirty"]):
        raise ValueError("Primary M4 execution requires a clean code commit")
    context_path = output / "execution_context.json"
    context = {
        "environment": environment,
        "source_content_hash": source_content_hash(root),
        "scope": plan["scope"],
        "plan_hash": plan["plan_hash"],
        "manifest_content_sha256": manifest["manifest_content_sha256"],
    }
    if context_path.exists():
        if not resume:
            raise ValueError("Output already contains an experiment; use --resume")
        saved = read_json(context_path)
        for key in ("source_content_hash", "scope", "plan_hash", "manifest_content_sha256"):
            if saved[key] != context[key]:
                raise ValueError(f"Resume context mismatch: {key}")
        for key in ("python", "dependencies", "dependency_lock_lf_sha256"):
            if saved["environment"][key] != environment[key]:
                raise ValueError(f"Resume environment mismatch: {key}")
        context = saved
    else:
        write_json(context_path, context, immutable=True)
    from km_quantum.m4.verification import verify_m4

    gate = verify_m4(root, output, config, manifest)
    if gate["status"] != "PASS":
        return {"status": "BLOCKED_GATE", "verification": gate, **preview}
    references = load_m4_references(root)
    entries = {row["reference_id"]: row for row in manifest["references"]}
    tables = {
        name: build_plan_table(references[name], config.max_qubits)
        for name in plan["reference_ids"]
    }
    started = invocation_started
    failures, stopped, completed = [], False, 0

    def check_wall() -> None:
        if perf_counter() - started >= config.max_wall_seconds:
            raise TimeoutError("Frozen M4 wall limit reached; remaining records stay visible")

    def notify(message: str) -> None:
        if progress:
            progress(message)

    try:
        with threadpool_limits(limits=config.max_parallel_threads):
            for name in plan["reference_ids"]:
                check_wall()
                reference, entry, table = references[name], entries[name], tables[name]
                identity = _identity(reference, entry, manifest, context)
                preparation_path = output / "preparations" / f"{name}.json"
                if preparation_path.exists():
                    preparation = read_json(preparation_path)
                    verify_seal(output, preparation_path.relative_to(output), preparation)
                    _validate_identity(preparation, reference, entry, manifest, context)
                    relaxation = preparation["relaxation"]
                else:
                    relaxation = solve_box_relaxation(
                        Polynomial(
                            number_variables=reference.qubo.number_variables,
                            constant=reference.qubo.constant,
                            linear=reference.qubo.linear,
                            quadratic=reference.qubo.quadratic,
                        ),
                        RelaxationConfig(
                            max_function_evaluations=config.relaxation_max_evaluations,
                            max_iterations=config.relaxation_max_iterations,
                            ftol=config.relaxation_ftol,
                            gtol=config.relaxation_gtol,
                            max_variables=config.max_qubits,
                        ),
                    )
                    preparation = {**identity, "status": "COMPLETE", "relaxation": relaxation}
                    write_json(preparation_path, preparation, immutable=True)
                    append_seal(output, preparation_path.relative_to(output), preparation)
                for expected in [r for r in plan["controls"] if r["reference_id"] == name]:
                    control_path = output / "controls" / f"{expected['control_id']}.json"
                    values = (
                        np.full(
                            1 << reference.qubo.number_variables,
                            1 / (1 << reference.qubo.number_variables),
                        )
                        if expected["control"] == "uniform"
                        else warm_product_probabilities(relaxation["y"], epsilon=config.epsilon)
                    )
                    if expected["control"] == "rounded_relaxation":
                        values = np.zeros(1 << reference.qubo.number_variables)
                        values[m2_bits_to_basis_index(round_relaxation(relaxation["y"]))] = 1
                    control_metrics = analyze_distribution(reference, table, values)
                    counts = multinomial_counts(
                        values, shots=config.shots, seed=expected["sampling_seed"]
                    )
                    control_sample = sample_metrics(
                        reference,
                        table,
                        counts,
                        shots=config.shots,
                        seed=expected["sampling_seed"],
                        probabilities=values,
                    )
                    control = {
                        **identity,
                        **expected,
                        "status": "COMPLETE",
                        "metrics": control_metrics,
                        "probabilities": values.tolist(),
                        "sampling": control_sample,
                        "relaxation": relaxation,
                        "cp_sat_reference": entry["cp_sat_reference"],
                        "classical_distribution_only": True,
                    }
                    if control_path.exists():
                        verify_seal(
                            output, control_path.relative_to(output), read_json(control_path)
                        )
                        _assert_record_matches(control, read_json(control_path), "control record")
                    else:
                        write_json(control_path, control, immutable=True)
                        append_seal(output, control_path.relative_to(output), control)
                for expected_training in [r for r in plan["training"] if r["reference_id"] == name]:
                    training_id = expected_training["training_id"]
                    training_path = output / "training" / f"{training_id}.json"
                    trace = output / "traces" / f"{training_id}.jsonl"
                    active_noise_id = None
                    try:
                        check_wall()
                        if training_path.exists():
                            verify_seal(
                                output, training_path.relative_to(output), read_json(training_path)
                            )
                            training, bound, ideal = _load_training(
                                training_path,
                                expected_training,
                                reference,
                                table,
                                entry,
                                config,
                                manifest,
                                context,
                                output,
                            )
                        else:
                            training, bound, ideal = train_variant(
                                reference,
                                algorithm=expected_training["algorithm"],
                                p=expected_training["p"],
                                seed=expected_training["initialization_seed"],
                                config=config,
                                trace_path=trace,
                                relaxation=relaxation,
                                table=table.state_table,
                            )
                            training.update(identity)
                            training.update(
                                {
                                    "training_id": training_id,
                                    "ideal_probabilities": ideal.tolist(),
                                    "ideal_metrics": analyze_distribution(reference, table, ideal),
                                    "trace_lf_sha256": lf_file_hash(trace),
                                }
                            )
                            write_json(training_path, training, immutable=True)
                            append_seal(output, training_path.relative_to(output), training)
                        compiled = compile_bound_circuit(
                            bound,
                            CompilerConfig(seed=config.transpiler_seed),
                            resource_limits(config),
                        )
                        compiled_path = output / "compiled" / f"{training_id}.json"
                        if compiled_path.exists():
                            archived_compiler = read_json(compiled_path)
                            verify_seal(
                                output, compiled_path.relative_to(output), archived_compiler
                            )
                            _assert_record_matches(
                                {
                                    k: v
                                    for k, v in compiled.metadata.items()
                                    if k != "compile_runtime_ms"
                                },
                                {
                                    k: v
                                    for k, v in archived_compiler.items()
                                    if k != "compile_runtime_ms"
                                },
                                "reconstructed compiler",
                            )
                        else:
                            archived_compiler = compiled.metadata
                            write_json(compiled_path, archived_compiler, immutable=True)
                            append_seal(
                                output, compiled_path.relative_to(output), archived_compiler
                            )
                        compiler = {
                            k: v for k, v in archived_compiler.items() if k != "gate_sequence"
                        }
                        for expected in [
                            r for r in plan["noise"] if r["training_id"] == training_id
                        ]:
                            active_noise_id = expected["run_id"]
                            check_wall()
                            path = output / "records" / f"{expected['run_id']}.json"
                            if path.exists():
                                verify_seal(output, path.relative_to(output), read_json(path))
                                _validate_saved_noise(
                                    read_json(path),
                                    expected,
                                    training,
                                    reference,
                                    table,
                                    entry,
                                    compiled,
                                    config,
                                    manifest,
                                    context,
                                )
                                completed += 1
                                continue
                            before, observed, noise_metadata = evaluate_noise(
                                compiled,
                                NoiseProfile(**expected["noise_profile"]),
                                resource_limits(config),
                            )
                            sampled_started = perf_counter()
                            counts = multinomial_counts(
                                observed, shots=config.shots, seed=expected["sampling_seed"]
                            )
                            sampled = sample_metrics(
                                reference,
                                table,
                                counts,
                                shots=config.shots,
                                seed=expected["sampling_seed"],
                                probabilities=observed,
                            )
                            sampling_ms = (perf_counter() - sampled_started) * 1000
                            analysis_started = perf_counter()
                            before_metrics = analyze_distribution(reference, table, before)
                            observed_metrics = analyze_distribution(reference, table, observed)
                            distribution_analysis_ms = (perf_counter() - analysis_started) * 1000
                            if not sampled["sampling_verified"]:
                                raise ValueError("Finite-sampling statistical audit failed")
                            if expected["noise_profile"]["name"] == "ideal" and not np.allclose(
                                ideal, before, atol=1e-9, rtol=1e-12
                            ):
                                raise ValueError("Run-specific zero-noise equivalence failed")
                            prep_ms = (
                                relaxation["runtime_ms"]
                                if expected["algorithm"] == "warm_start"
                                else 0.0
                            )
                            base_ms = (
                                training["runtime_ms"]
                                + compiler["compile_runtime_ms"]
                                + noise_metadata["density_runtime_ms"]
                                + noise_metadata["readout_runtime_ms"]
                                + sampling_ms
                                + distribution_analysis_ms
                            )
                            training_summary = {
                                key: training[key]
                                for key in (
                                    "nfev",
                                    "success",
                                    "termination_message",
                                    "initial_parameters",
                                    "final_parameters",
                                    "parameter_order",
                                    "parameter_bindings",
                                    "initial_expected_energy",
                                    "final_expected_energy",
                                    "energy_offset",
                                    "runtime_ms",
                                    "timings",
                                    "circuit",
                                    "preparation_quality_gate",
                                )
                            }
                            row = {
                                **identity,
                                **expected,
                                "status": "COMPLETE",
                                "final_parameters": training["final_parameters"],
                                "training": training_summary,
                                "training_path": f"training/{training_id}.json",
                                "relaxation": relaxation,
                                "compiler": compiler,
                                "compiled_artifact": f"compiled/{training_id}.json",
                                "probabilities": {
                                    "ideal": ideal.tolist(),
                                    "before_readout": before.tolist(),
                                    "after_readout": observed.tolist(),
                                },
                                "metrics": {
                                    "ideal": training["ideal_metrics"],
                                    "before_readout": before_metrics,
                                    "after_readout": observed_metrics,
                                },
                                "sampling": sampled,
                                "noise_metadata": noise_metadata,
                                "timings": {
                                    "ideal_training_runtime_ms": training["runtime_ms"],
                                    "relaxation_runtime_ms": prep_ms,
                                    "compile_runtime_ms": compiler["compile_runtime_ms"],
                                    "density_runtime_ms": noise_metadata["density_runtime_ms"],
                                    "readout_runtime_ms": noise_metadata["readout_runtime_ms"],
                                    "sampling_analysis_runtime_ms": sampling_ms,
                                    "distribution_analysis_runtime_ms": distribution_analysis_ms,
                                    "standalone_end_to_end_ms": base_ms + prep_ms,
                                    "amortized_end_to_end_ms": base_ms
                                    + prep_ms / (len(config.depths) * len(config.restart_seeds)),
                                    "scope": (
                                        "Standalone pipeline counts preparation and ideal training "
                                        "fully; transferred-profile reuse and campaign actual "
                                        "wall time reported separately."
                                    ),
                                },
                                "hardware_used": False,
                                "training_noise_used": False,
                            }
                            write_json(path, row, immutable=True)
                            append_seal(output, path.relative_to(output), row)
                            completed += 1
                            if completed % 25 == 0 or completed == len(plan["noise"]):
                                notify(
                                    f"M4 {plan['scope']}: {completed}/"
                                    f"{len(plan['noise'])} executions"
                                )
                    except Exception as error:
                        failure = {
                            **identity,
                            **expected_training,
                            "status": "FAILED",
                            "error_type": type(error).__name__,
                            "error": str(error),
                            "failed_execution_id": active_noise_id,
                        }
                        failures.append(failure)
                        write_json(
                            output / "failures" / f"{training_id}.json", failure, immutable=True
                        )
                        if isinstance(error, TimeoutError):
                            stopped = True
                            break
                if stopped:
                    break
    except Exception as error:
        failure = {
            "status": "FAILED",
            "training_id": "campaign_abort",
            "failed_execution_id": None,
            "error_type": type(error).__name__,
            "error": str(error),
            "scope": plan["scope"],
        }
        failures.append(failure)
        stopped = isinstance(error, TimeoutError)
        write_json(output / "failures/campaign_abort.json", failure, immutable=True)
    records, controls = load_records(output)
    _record_jsonl(output / "runs.jsonl", records)
    _record_jsonl(output / "controls.jsonl", controls)
    failures = [read_json(path) for path in sorted((output / "failures").glob("*.json"))]
    complete_ids = {row["run_id"] for row in records if row["status"] == "COMPLETE"}
    failed_ids = {row["failed_execution_id"] for row in failures}
    failed_training = {row["training_id"] for row in failures}
    ledger = []
    for expected in plan["noise"]:
        identifier = expected["run_id"]
        status = (
            "COMPLETE"
            if identifier in complete_ids
            else "FAILED"
            if identifier in failed_ids
            else "SKIPPED_WALL_LIMIT"
            if stopped
            else "SKIPPED_DEPENDENCY_FAILURE"
            if expected["training_id"] in failed_training
            else "MISSING"
        )
        ledger.append({**expected, "status": status})
    _record_jsonl(output / "execution_ledger.jsonl", ledger)
    result = {
        "status": "COMPLETE"
        if len(records) == len(plan["noise"]) and not failures
        else "INCOMPLETE",
        **preview,
        "completed_executions": len(records),
        "failures": failures,
        "stopped_at_wall_limit": stopped,
        "duration_seconds": perf_counter() - started,
        "code_sha": context["environment"]["git_commit"],
        "config_hash": manifest["config_hash"],
        "manifest_content_sha256": manifest["manifest_content_sha256"],
    }
    write_json(output / "benchmark_execution.json", result)
    return result
