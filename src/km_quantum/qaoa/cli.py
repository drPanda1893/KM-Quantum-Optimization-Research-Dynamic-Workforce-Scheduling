"""Scriptable local M3 workflow over frozen references, with full trace persistence."""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter
from typing import Any

from km_quantum.experiments.io import read_json, read_model, write_json
from km_quantum.qaoa.models import QAOAConfig


def add_qaoa_parser[ParserT: argparse.ArgumentParser](
    commands: argparse._SubParsersAction[ParserT],
) -> None:
    parser = commands.add_parser("qaoa", help="M3 ideal standard-QAOA on frozen M2 references")
    actions = parser.add_subparsers(dest="qaoa_command", required=True, parser_class=type(parser))
    manifest = actions.add_parser("manifest", help="Freeze the structural benchmark manifest")
    inspect = actions.add_parser(
        "inspect", help="Inspect Hamiltonian, offset, ordering and circuit"
    )
    inspect.add_argument("--reference", required=True)
    inspect.add_argument("--p", type=int, default=1)
    verify = actions.add_parser("verify-operator", help="Check every basis energy before QAOA")
    verify.add_argument("--reference", help="Default: all frozen references within max-qubits")
    verify.add_argument("--max-qubits", type=int, default=14)
    verify.add_argument("--output")
    run = actions.add_parser("run", help="Exact COBYLA training followed by ideal seeded sampling")
    run.add_argument("--reference", required=True)
    run.add_argument("--config", help="QAOAConfig JSON")
    run.add_argument("--p", type=int)
    run.add_argument("--seed", dest="initialization_seed", type=int)
    run.add_argument("--maxiter", type=int)
    run.add_argument("--shots", type=int)
    run.add_argument("--sampler-seed", type=int)
    run.add_argument("--output", required=True)
    run.add_argument("--trace-output", help="Default: output with .trace.jsonl suffix")
    sample = actions.add_parser("sample", help="Resample a saved final ideal circuit")
    sample.add_argument("--run", required=True)
    sample.add_argument("--shots", type=int, default=1024)
    sample.add_argument("--seed", type=int, default=0)
    sample.add_argument("--output", required=True)
    benchmark = actions.add_parser("benchmark", help="Run the fixed multi-restart depth study")
    benchmark.add_argument("--output-directory", default="results/m3")
    benchmark.add_argument("--split", choices=("all", "development", "evaluation"), default="all")
    benchmark.add_argument("--resume", action="store_true")
    acceptance = actions.add_parser(
        "acceptance", help="Audit complete benchmark and tooling evidence"
    )
    acceptance.add_argument("--output-directory", default="results/m3")
    acceptance.add_argument("--tooling", help="JSON of pytest/lint/format/typecheck/build evidence")
    for action in (manifest, inspect, verify, run, sample, benchmark, acceptance):
        action.add_argument("--root", default=".", help="Repository containing frozen M2 artifacts")


def _configuration(args: argparse.Namespace) -> QAOAConfig:
    initial = read_model(args.config, QAOAConfig) if args.config else QAOAConfig()
    data = initial.model_dump()
    for name in ("p", "initialization_seed", "maxiter", "shots", "sampler_seed"):
        value = getattr(args, name, None)
        if value is not None:
            data[name] = value
    if args.sampler_seed is None and not args.config:
        data["sampler_seed"] = 10000 + data["initialization_seed"] + 100 * data["p"]
    return QAOAConfig.model_validate(data)


def execute_qaoa(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from km_quantum.qaoa.analysis import build_state_table
    from km_quantum.qaoa.operators import ising_to_operator, verify_operator
    from km_quantum.qaoa.references import load_references
    from km_quantum.qaoa.sampling import sample_state
    from km_quantum.qaoa.simulation import bind_parameters, build_qaoa, circuit_metrics, run_qaoa

    root = Path(args.root).resolve()
    command = args.qaoa_command
    from km_quantum.qaoa.benchmark import build_acceptance, create_manifest, run_benchmark

    if command == "manifest":
        return create_manifest(root), 0
    if command == "benchmark":
        report = run_benchmark(
            root, Path(args.output_directory), split=args.split, resume=args.resume
        )
        return report, 0 if report.get("status") in ("PASS", "COMPLETE") else 1
    if command == "acceptance":
        tooling = read_json(args.tooling) if args.tooling else None
        report = build_acceptance(root, Path(args.output_directory), tooling=tooling)
        return report, 0 if report["status"] == "PASS" else 1
    references = load_references(root)
    if command == "verify-operator":
        if args.reference and args.reference not in references:
            raise ValueError(f"Unknown frozen reference: {args.reference}")
        selected = (
            [references[args.reference]]
            if args.reference
            else [
                reference
                for reference in references.values()
                if reference.qubo.number_variables <= args.max_qubits
            ]
        )
        if not selected:
            raise ValueError("No frozen references lie within the requested qubit bound")
        results = {
            reference.name: {
                **verify_operator(reference.ising, args.max_qubits),
                "ising_hash": reference.ising_hash,
            }
            for reference in selected
        }
        report = {
            "status": "PASS" if all(row["passed"] for row in results.values()) else "FAIL",
            "reference_count": len(results),
            "checked_states": sum(row["checked_states"] for row in results.values()),
            "mismatch_count": sum(row["mismatch_count"] for row in results.values()),
            "references": results,
        }
        if args.output:
            write_json(args.output, report)
        return report, 0 if report["status"] == "PASS" else 1
    saved: dict[str, Any] = read_json(args.run) if command == "sample" else {}
    if command == "sample" and (
        not isinstance(saved, dict)
        or not {
            "reference_name",
            "m2_reference_hash",
            "p",
            "parameter_order",
            "final_parameters",
            "ground_state_probability",
        }
        <= saved.keys()
        or not isinstance(saved["reference_name"], str)
    ):
        raise ValueError("Saved run is missing required reference/parameter/probability fields")
    name = args.reference if command != "sample" else saved["reference_name"]
    if name not in references:
        raise ValueError(f"Unknown frozen reference: {name}")
    reference = references[name]
    if command == "inspect":
        prepared = build_qaoa(reference.ising, args.p)
        operator = ising_to_operator(reference.ising)
        return {
            "reference": name,
            "m2_reference_hash": reference.reference_hash,
            "energy_offset": operator.energy_offset,
            "operator_terms": [
                (label, coefficient.real)
                for label, coefficient in operator.circuit_hamiltonian.to_list()
            ],
            "circuit": circuit_metrics(prepared),
            "variable_mapping": reference.qubo.mapping.model_dump(mode="json"),
            "ground_bits_m2_order": list(reference.ground_bits),
            "ground_energy": reference.ground_energy,
            "bit_order": "M2 x[i] maps to Qiskit q[i]; displayed rightmost bit is index zero",
        }, 0
    table = build_state_table(reference)
    if command == "sample":
        if saved["m2_reference_hash"] != reference.reference_hash:
            raise ValueError("Saved run refers to a different frozen M2 artifact")
        prepared = build_qaoa(reference.ising, saved["p"])
        if saved["parameter_order"] != [parameter.name for parameter in prepared.parameters]:
            raise ValueError("Saved parameter ordering differs from this Qiskit circuit")
        bound = bind_parameters(prepared, saved["final_parameters"])
        sampled = sample_state(
            reference,
            bound,
            table,
            shots=args.shots,
            seed=args.seed,
            exact_ground_probability=saved["ground_state_probability"],
        )
        write_json(args.output, sampled)
        return sampled, 0 if sampled["sampling_verified"] else 1
    config = _configuration(args)
    output = Path(args.output)
    if output.exists():
        raise ValueError(f"Run output already exists: {output}")
    trace_path = args.trace_output or str(output.with_suffix(".trace.jsonl"))
    started = perf_counter()
    result, bound, _ = run_qaoa(reference, config, trace_path=trace_path, table=table)
    result["sampling"] = sample_state(
        reference,
        bound,
        table,
        shots=config.shots,
        seed=config.sampler_seed,
        exact_ground_probability=result["ground_state_probability"],
    )
    result["total_run_runtime_ms"] = (perf_counter() - started) * 1000
    write_json(output, result)
    return result, 0 if result["sampling"]["sampling_verified"] else 1
