"""Exercise persistent experiment artifacts and the public CLI workflow."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from km_quantum.__main__ import main
from km_quantum.domain import SchedulingInstance, SolverConfig, SolverResult
from km_quantum.experiments import BenchmarkResult, append_benchmark, run_benchmark
from km_quantum.experiments.io import canonical_hash, read_json, read_model, write_json
from km_quantum.instances import GeneratorConfig, generate_instance, hand_cases


def test_instance_json_roundtrip_and_stable_hash(tmp_path: Path) -> None:
    instance = generate_instance(GeneratorConfig(seed=123))
    path = tmp_path / "nested" / "instance.json"
    write_json(path, instance)
    restored = read_model(path, SchedulingInstance)
    assert restored == instance
    assert canonical_hash(restored) == canonical_hash(instance)
    # Field order and indentation are irrelevant to the artifact identity.
    reordered = dict(reversed(list(read_json(path).items())))
    assert canonical_hash(reordered) == canonical_hash(instance)


@pytest.mark.parametrize("solver", ["cp-sat", "exhaustive"])
def test_benchmark_record_is_reproducible_and_revalidatable(tmp_path: Path, solver: str) -> None:
    from typing import cast

    from km_quantum.experiments.benchmark import SolverName

    instance = hand_cases()["priority_overlap"]
    config = SolverConfig(random_seed=42)
    record = run_benchmark(
        instance, config, experiment_id="reference", solver=cast(SolverName, solver)
    )
    restored = BenchmarkResult.model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.feasible and restored.proven_optimal
    assert restored.objective == 100000
    assert restored.assignment_count == 1 and restored.unassigned_count == 1
    assert restored.violations == ()
    assert restored.instance_hash == canonical_hash(instance)
    assert restored.timestamp.utcoffset() is not None
    assert restored.environment["python"]
    assert restored.environment["ortools"]
    assert SchedulingInstance.model_validate(restored.instance) == instance
    assert SolverConfig.model_validate(restored.solver_config) == config
    result = SolverResult.model_validate(restored.raw_result)
    assert result.objective_value == restored.objective
    assert restored.objective_breakdown is not None
    assert restored.objective_breakdown["total"] == restored.objective
    path = tmp_path / "runs.jsonl"
    append_benchmark(path, record)
    append_benchmark(path, record)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(BenchmarkResult.model_validate_json(line) == record for line in lines)


def test_benchmark_infeasible_has_no_fabricated_solution_metrics() -> None:
    record = run_benchmark(hand_cases()["mandatory_infeasible"], experiment_id="infeasible")
    assert record.status == "INFEASIBLE"
    assert not record.feasible and not record.proven_optimal
    assert record.objective is None
    assert record.assignment_count is None and record.unassigned_count is None
    assert record.objective_breakdown is None
    assert record.raw_result["schedule"] is None


def test_benchmark_rejects_misreported_objective(monkeypatch: pytest.MonkeyPatch) -> None:
    from km_quantum.experiments import benchmark

    instance = hand_cases()["touching_intervals"]
    original = benchmark.solve_instance(instance, SolverConfig())
    assert original.schedule is not None
    inaccurate_schedule = original.schedule.model_copy(update={"objective_value": 999})
    inaccurate_result = original.model_copy(
        update={"schedule": inaccurate_schedule, "objective_value": 999}
    )
    monkeypatch.setattr(benchmark, "solve_instance", lambda *_args: inaccurate_result)
    with pytest.raises(ValueError, match="independent objective"):
        run_benchmark(instance, experiment_id="wrong-objective")


@pytest.mark.parametrize("override", [
    {"runtime_ms": float("inf")},
    {"best_bound": float("nan")},
    {"optimality_gap": -1},
    {"timestamp": "2026-10-07T00:00:00"},
    {"proven_optimal": False},
    {"unassigned_count": -1},
])
def test_benchmark_schema_rejects_unusable_metrics(override: dict[str, object]) -> None:
    record = run_benchmark(hand_cases()["touching_intervals"], experiment_id="schema-check")
    raw = record.model_dump(mode="json")
    raw.update(override)
    with pytest.raises(ValidationError):
        BenchmarkResult.model_validate(raw)


def test_cli_generate_solve_validate_benchmark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance_path = tmp_path / "instance.json"
    result_path = tmp_path / "result.json"
    benchmark_path = tmp_path / "results.jsonl"
    assert main([
        "generate", "--workers", "2", "--jobs", "3", "--skills", "1",
        "--compatibility-density", "1", "--overlap-density", "0",
        "--workload-tightness", "0", "--seed", "17", "--output", str(instance_path),
    ]) == 0
    instance = read_model(instance_path, SchedulingInstance)
    assert instance.seed == 17 and len(instance.jobs) == 3
    assert main(["solve", "--instance", str(instance_path), "--output", str(result_path)]) == 0
    result = read_model(result_path, SolverResult)
    assert result.status == "OPTIMAL" and result.objective_value == 0
    assert main(["validate", "--instance", str(instance_path), "--schedule", str(result_path)]) == 0
    assert main([
        "benchmark", "--instance", str(instance_path), "--experiment-id", "smoke",
        "--output", str(benchmark_path),
    ]) == 0
    outputs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(outputs) == 4
    assert outputs[2]["is_valid"] and outputs[2]["violation_count"] == 0
    assert outputs[3]["objective"] == 0 and outputs[3]["feasible"]
    assert BenchmarkResult.model_validate_json(benchmark_path.read_text()).instance_hash == (
        canonical_hash(instance)
    )


def test_cli_validates_malformed_candidates_structurally(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance_path, schedule_path = tmp_path / "instance.json", tmp_path / "candidate.json"
    write_json(instance_path, hand_cases()["touching_intervals"])
    write_json(schedule_path, {"assignments": [{"job_id": "job-a"}]})
    assert main([
        "validate", "--instance", str(instance_path), "--schedule", str(schedule_path)
    ]) == 1
    validation = json.loads(capsys.readouterr().out)
    assert validation["is_valid"] is False
    assert validation["violation_count"] == 1
    assert validation["violations"][0]["code"] == "invalid_structure"


def test_cli_infeasible_result_is_saved_and_cannot_be_validated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance_path, result_path = tmp_path / "instance.json", tmp_path / "result.json"
    write_json(instance_path, hand_cases()["mandatory_infeasible"])
    assert main(["solve", "--instance", str(instance_path), "--output", str(result_path)]) == 1
    assert read_json(result_path)["schedule"] is None
    capsys.readouterr()
    assert main(["validate", "--instance", str(instance_path), "--schedule", str(result_path)]) == 1
    validation = json.loads(capsys.readouterr().out)
    assert validation["violations"][0]["code"] == "missing_schedule"


def test_cli_config_and_overrides_are_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance_path, config_path = tmp_path / "instance.json", tmp_path / "config.json"
    output = tmp_path / "result.jsonl"
    write_json(instance_path, hand_cases()["cost_choice"])
    write_json(config_path, SolverConfig().model_dump(mode="json"))
    assert main([
        "benchmark", "--instance", str(instance_path), "--config", str(config_path),
        "--cost-weight", "1", "--random-seed", "19", "--solver", "exhaustive",
        "--experiment-id", "cost", "--output", str(output),
    ]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["objective"] == 1500
    assert record["solver_config"]["objective"]["cost_weight"] == 1
    assert record["solver_config"]["random_seed"] == 19


def test_cli_input_error_is_json_and_does_not_write_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "instance.json"
    assert main(["generate", "--workers", "-1", "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert not captured.out and not output.exists()
    assert json.loads(captured.err)["error"]["code"] == "input_error"


def test_cli_module_entrypoint_and_usage_errors() -> None:
    help_result = subprocess.run(
        [sys.executable, "-m", "km_quantum", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert help_result.returncode == 0 and "benchmark" in help_result.stdout
    invalid_result = subprocess.run(
        [sys.executable, "-m", "km_quantum", "unknown-command"],
        capture_output=True, text=True, check=False,
    )
    assert invalid_result.returncode == 2
    assert json.loads(invalid_result.stderr)["error"]["code"] == "usage_error"
