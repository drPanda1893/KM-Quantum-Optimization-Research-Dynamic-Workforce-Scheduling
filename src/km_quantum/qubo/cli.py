"""M2 subcommands, isolated from the frozen classical workflow."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from km_quantum.domain import SchedulingInstance
from km_quantum.experiments.io import read_model, write_json
from km_quantum.experiments.m2_benchmark import append_m2_benchmark, build_m2_benchmark
from km_quantum.qubo.encoder import encode_qubo
from km_quantum.qubo.exhaustive import solve_qubo
from km_quantum.qubo.ising import qubo_to_ising
from km_quantum.qubo.models import QUBOConfig, QUBOModel
from km_quantum.qubo.references import (
    infeasible_case,
    m2_hand_cases,
    penalty_sensitivity_case,
    random_small_cases,
)
from km_quantum.qubo.verification import VerificationResult, verify_equivalence


def add_qubo_parser[ParserT: argparse.ArgumentParser](
    commands: argparse._SubParsersAction[ParserT],
) -> None:
    parser = commands.add_parser("qubo", help="M2 exact encoding and equivalence workflow")
    actions = parser.add_subparsers(dest="qubo_command", required=True, parser_class=type(parser))
    encode = actions.add_parser("encode", help="Encode a fixed-slot instance as an integer QUBO")
    encode.add_argument("--instance", required=True)
    encode.add_argument("--config", help="QUBOConfig JSON; default mathematically safe penalties")
    encode.add_argument("--output", required=True)
    inspect = actions.add_parser("inspect", help="Inspect variables, penalties and size metrics")
    inspect.add_argument("--model", required=True)
    inspect.add_argument("--matrix", action="store_true", help="Include upper-triangular Q matrix")
    inspect.add_argument("--output")
    solve = actions.add_parser("solve-exhaustive", help="Enumerate the complete QUBO state space")
    solve.add_argument("--instance", required=True)
    solve.add_argument("--model", required=True)
    solve.add_argument("--max-states", type=int, default=1_048_576)
    solve.add_argument("--output", required=True)
    verify = actions.add_parser("verify", help="Cross-check all minima against both M1 oracles")
    verify.add_argument("--instance", required=True)
    source = verify.add_mutually_exclusive_group()
    source.add_argument("--model", help="Previously encoded QUBO JSON")
    source.add_argument("--config", help="QUBOConfig JSON, if encoding a fresh model")
    verify.add_argument("--max-states", type=int, default=1_048_576)
    verify.add_argument("--output", help="Verification JSON")
    verify.add_argument("--benchmark-output", help="Append a schema-version-2 JSONL record")
    verify.add_argument("--experiment-id", default="m2-equivalence")
    ising = actions.add_parser("to-ising", help="Convert a QUBO to exact scaled-integer Ising")
    ising.add_argument("--model", required=True)
    ising.add_argument("--output", required=True)
    acceptance = actions.add_parser("acceptance", help="Run the frozen M2 acceptance suite")
    acceptance.add_argument("--output", required=True, help="Acceptance report JSON")
    acceptance.add_argument("--benchmark-output", help="Append all raw case records as JSONL")
    acceptance.add_argument("--experiment-id", default="m2-acceptance")
    acceptance.add_argument("--max-states", type=int, default=1_048_576)
    acceptance.add_argument("--random-cases", type=int, default=100, help="At least 100 cases")


def _config(path: str | None) -> QUBOConfig:
    return read_model(path, QUBOConfig) if path else QUBOConfig()


def _summary(name: str, model: QUBOModel, result: VerificationResult) -> dict[str, Any]:
    qubo = result.qubo_result
    return {
        "name": name,
        "instance_id": model.mapping.instance_id,
        "status": result.status,
        "equivalence_passed": result.equivalence_passed,
        "ising_equivalence_passed": result.ising_result.equivalent,
        "classical_objective": result.classical_objective,
        "ground_energy": qubo.ground_energy,
        "ground_state_count": qubo.ground_state_degeneracy,
        "invalid_ground_state_count": sum(
            not state.encoded_feasible for state in qubo.ground_states
        ),
        "constraint_energy_margin": qubo.invalid_energy_margin,
        "best_valid_energy": qubo.best_valid_energy,
        "state_count": qubo.state_count,
        "qubo_variable_count": model.number_variables,
        "penalties": model.penalties.model_dump(mode="json"),
        "metrics": model.metrics().model_dump(mode="json"),
    }


def run_acceptance(
    *,
    random_cases: int = 100,
    max_states: int = 1_048_576,
    benchmark_output: str | None = None,
    experiment_id: str = "m2-acceptance",
) -> dict[str, Any]:
    """PASS only for a full feasible suite, expected sensitivity failures and infeasibility."""
    if random_cases < 100:
        raise ValueError("The M2 acceptance gate requires at least 100 frozen random cases")
    suite = m2_hand_cases() | random_small_cases(random_cases)
    summaries: list[dict[str, Any]] = []

    def run_case(name: str, instance: SchedulingInstance, config: QUBOConfig) -> VerificationResult:
        model = encode_qubo(instance, config)
        result = verify_equivalence(instance, model=model, max_states=max_states)
        summaries.append(_summary(name, model, result))
        if benchmark_output:
            append_m2_benchmark(
                benchmark_output,
                build_m2_benchmark(
                    instance, model, result, experiment_id=f"{experiment_id}/{name}"
                ),
            )
        return result

    feasible_results = [
        run_case(name, instance, config) for name, (instance, config) in suite.items()
    ]
    infeasible_instance, infeasible_config = infeasible_case()
    infeasible_result = run_case("infeasible", infeasible_instance, infeasible_config)
    sensitivity_instance, sensitivity_config = penalty_sensitivity_case()
    sensitivity: list[dict[str, Any]] = []
    for scale in ("1/4", "1/2", "1", "2", "4"):
        data = sensitivity_config.model_dump()
        data["penalty_scale"] = scale
        config = QUBOConfig.model_validate(data)
        result = run_case(f"sensitivity-{scale}", sensitivity_instance, config)
        expected_status = "FAIL" if scale in ("1/4", "1/2") else "PASS"
        observed = summaries[-1].copy()
        observed["expected_status"] = expected_status
        observed["expected_outcome_observed"] = (
            result.status == expected_status and result.ising_result.equivalent
        )
        sensitivity.append(observed)
    passed = (
        all(
            result.equivalence_passed and result.ising_result.equivalent
            for result in feasible_results
        )
        and infeasible_result.status == "INFEASIBLE"
        and infeasible_result.ising_result.equivalent
        and all(row["expected_outcome_observed"] for row in sensitivity)
    )
    return {
        "schema_version": 1,
        "artifact_type": "m2_acceptance",
        "status": "PASS" if passed else "FAIL",
        "m2_equivalence": "PASS" if passed else "FAIL",
        "timestamp": datetime.now(UTC).isoformat(),
        "hand_case_count": len(m2_hand_cases()),
        "random_case_count": random_cases,
        "successful_feasible_cases": sum(result.equivalence_passed for result in feasible_results),
        "feasible_suite_case_count": len(feasible_results),
        "enumerated_bitstrings": sum(row["state_count"] for row in summaries),
        "ground_states_analyzed": sum(row["ground_state_count"] for row in summaries),
        "infeasible_status": infeasible_result.status,
        "penalty_sensitivity": sensitivity,
        "cases": summaries,
    }


def execute_qubo(args: argparse.Namespace) -> tuple[BaseModel | dict[str, Any], int]:
    command = args.qubo_command
    if command == "acceptance":
        report = run_acceptance(
            random_cases=args.random_cases,
            max_states=args.max_states,
            benchmark_output=args.benchmark_output,
            experiment_id=args.experiment_id,
        )
        write_json(args.output, report)
        return report, 0 if report["status"] == "PASS" else 1
    if command == "encode":
        instance = read_model(args.instance, SchedulingInstance)
        model = encode_qubo(instance, _config(args.config))
        write_json(args.output, model)
        return model, 0
    if command == "inspect":
        model = read_model(args.model, QUBOModel)
        inspection: dict[str, Any] = {
            "instance_id": model.mapping.instance_id,
            "metrics": model.metrics().model_dump(mode="json"),
            "variables": model.mapping.model_dump(mode="json"),
            "penalties": model.penalties.model_dump(mode="json"),
            "config": model.config.model_dump(mode="json"),
        }
        if args.matrix:
            inspection["matrix"] = model.to_matrix()
            inspection["matrix_convention"] = "upper-triangular: x.T Q x + constant"
            inspection["constant"] = model.constant
        if args.output:
            write_json(args.output, inspection)
        return inspection, 0
    if command == "to-ising":
        model = read_model(args.model, QUBOModel)
        ising = qubo_to_ising(model)
        write_json(args.output, ising)
        return ising, 0
    instance = read_model(args.instance, SchedulingInstance)
    if command == "solve-exhaustive":
        model = read_model(args.model, QUBOModel)
        solved = solve_qubo(model, instance, max_states=args.max_states)
        write_json(args.output, solved)
        # A proven polynomial minimum can still encode an invalid M1 schedule.
        valid = all(
            state.encoded_feasible and state.validation.is_valid for state in solved.ground_states
        )
        return solved, 0 if valid else 1
    model = (
        read_model(args.model, QUBOModel)
        if args.model
        else encode_qubo(instance, _config(args.config))
    )
    verified = verify_equivalence(instance, model=model, max_states=args.max_states)
    if args.output:
        write_json(args.output, verified)
    if args.benchmark_output:
        append_m2_benchmark(
            args.benchmark_output,
            build_m2_benchmark(instance, model, verified, experiment_id=args.experiment_id),
        )
    return verified, 0 if verified.equivalence_passed else 1
