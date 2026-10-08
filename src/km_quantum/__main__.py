"""Scriptable M1 CLI: generate, solve, validate and append benchmark records."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Never, cast

from pydantic import BaseModel, ValidationError

from km_quantum.domain import SchedulingInstance, SolverConfig
from km_quantum.experiments import append_benchmark, run_benchmark, solve_instance
from km_quantum.experiments.benchmark import SolverName
from km_quantum.experiments.io import read_json, read_model, write_json
from km_quantum.instances import GeneratorConfig, generate_instance
from km_quantum.metrics import evaluate_objective, validate_schedule


def _emit(value: BaseModel | dict[str, Any], *, error: bool = False) -> None:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    print(
        json.dumps(data, ensure_ascii=False, allow_nan=False),
        file=sys.stderr if error else sys.stdout,
    )


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        _emit({"error": {"code": "usage_error", "message": message}}, error=True)
        self.exit(2)


def _priority_weights(value: str) -> tuple[float, ...]:
    try:
        return tuple(float(weight.strip()) for weight in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use comma-separated numeric priority weights") from exc


def _solver_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--instance", required=True, help="Input instance JSON")
    parser.add_argument(
        "--config", help="SolverConfig JSON; defaults to configs/baseline.json values"
    )
    parser.add_argument("--solver", choices=("cp-sat", "exhaustive"), default="cp-sat")
    parser.add_argument("--time-limit-seconds", type=float)
    parser.add_argument("--num-search-workers", type=int)
    parser.add_argument("--random-seed", type=int)
    parser.add_argument("--max-deterministic-time", type=float)
    parser.add_argument("--exhaustive-max-states", type=int)
    parser.add_argument("--unassigned-penalty", type=int)
    parser.add_argument("--cost-weight", type=int)
    parser.add_argument("--imbalance-weight", type=int)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="KM³ M1 classical scheduling research workflow")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    generate = commands.add_parser("generate", help="Generate a deterministic synthetic instance")
    generate.add_argument("--output", required=True, help="Destination instance JSON")
    generate.add_argument("--config", help="GeneratorConfig JSON")
    generate.add_argument("--workers", "--number-of-workers", dest="number_of_workers", type=int)
    generate.add_argument("--jobs", "--number-of-jobs", dest="number_of_jobs", type=int)
    generate.add_argument("--skills", "--number-of-skills", dest="number_of_skills", type=int)
    generate.add_argument("--compatibility-density", type=float)
    generate.add_argument("--overlap-density", type=float)
    generate.add_argument("--workload-tightness", type=float)
    generate.add_argument("--priority-distribution", type=_priority_weights)
    generate.add_argument("--min-duration-minutes", type=int)
    generate.add_argument("--max-duration-minutes", type=int)
    generate.add_argument("--horizon-minutes", type=int)
    generate.add_argument("--must-assign-fraction", type=float)
    generate.add_argument("--min-cost-per-hour", type=int)
    generate.add_argument("--max-cost-per-hour", type=int)
    generate.add_argument("--seed", type=int)
    solve = commands.add_parser("solve", help="Solve and independently verify an instance")
    _solver_arguments(solve)
    solve.add_argument("--output", required=True, help="Destination SolverResult JSON")
    validate = commands.add_parser("validate", help="Independently validate a schedule/result JSON")
    validate.add_argument("--instance", required=True)
    validate.add_argument("--schedule", required=True, help="Schedule JSON or SolverResult JSON")
    validate.add_argument("--output", help="Optional destination validation JSON")
    benchmark = commands.add_parser("benchmark", help="Append one complete benchmark JSONL record")
    _solver_arguments(benchmark)
    benchmark.add_argument("--experiment-id", required=True)
    benchmark.add_argument("--output", required=True, help="Destination benchmark JSONL (append)")
    from km_quantum.qubo.cli import add_qubo_parser

    add_qubo_parser(commands)
    return parser


def _generator_config(args: argparse.Namespace) -> GeneratorConfig:
    initial = read_model(args.config, GeneratorConfig) if args.config else GeneratorConfig()
    data = initial.model_dump()
    for field in GeneratorConfig.model_fields:
        value = getattr(args, field, None)
        if value is not None:
            data[field] = value
    return GeneratorConfig.model_validate(data)


def _solver_config(args: argparse.Namespace) -> SolverConfig:
    initial = read_model(args.config, SolverConfig) if args.config else SolverConfig()
    data = initial.model_dump()
    for field in SolverConfig.model_fields:
        if field == "objective":
            continue
        value = getattr(args, field, None)
        if value is not None:
            data[field] = value
    for field in ("unassigned_penalty", "cost_weight", "imbalance_weight"):
        value = getattr(args, field, None)
        if value is not None:
            data["objective"][field] = value
    return SolverConfig.model_validate(data)


def _execute(args: argparse.Namespace) -> int:
    if args.command == "qubo":
        from km_quantum.qubo.cli import execute_qubo

        output, code = execute_qubo(args)
        _emit(output)
        return code
    if args.command == "generate":
        instance = generate_instance(_generator_config(args))
        write_json(args.output, instance)
        _emit(instance)
        return 0
    instance = read_model(args.instance, SchedulingInstance)
    if args.command == "validate":
        raw = read_json(args.schedule)
        if isinstance(raw, dict) and "schedule" in raw:
            raw = raw["schedule"]
        if raw is None:
            validation: BaseModel | dict[str, Any] = {
                "is_valid": False,
                "violation_count": 1,
                "violations": [
                    {
                        "code": "missing_schedule",
                        "message": "The solver result does not contain a schedule",
                        "job_id": None,
                        "worker_id": None,
                    }
                ],
            }
            is_valid = False
        else:
            checked = validate_schedule(instance, raw)
            validation = checked
            is_valid = checked.is_valid
        if args.output:
            write_json(args.output, validation)
        _emit(validation)
        return 0 if is_valid else 1
    config = _solver_config(args)
    solver = cast(SolverName, args.solver)
    if args.command == "benchmark":
        record = run_benchmark(instance, config, experiment_id=args.experiment_id, solver=solver)
        append_benchmark(args.output, record)
        _emit(record)
        return 0 if record.feasible else 1
    result = solve_instance(instance, config, solver)
    write_json(args.output, result)
    _emit(result)
    if result.schedule is None:
        return 1
    validation = validate_schedule(instance, result.schedule)
    if not validation.is_valid:
        _emit(
            {"error": {"code": "invalid_solver_schedule", "validation": validation.model_dump()}},
            error=True,
        )
        return 1
    objective = evaluate_objective(instance, result.schedule, config.objective)
    if objective.total != result.objective_value:
        raise ValueError("Solver objective disagrees with the independent objective evaluator")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Return 0 on a feasible result, 1 on no/invalid schedule, 2 on input/IO errors."""
    args = _parser().parse_args(argv)
    try:
        return _execute(args)
    except (ValidationError, ValueError, OSError) as exc:
        _emit({"error": {"code": "input_error", "message": str(exc)}}, error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
