"""M2 CLI/artifact persistence and the public automated acceptance gate."""

import json
from itertools import product
from pathlib import Path

import pytest

from km_quantum.__main__ import main
from km_quantum.domain import SchedulingInstance
from km_quantum.experiments.io import canonical_hash, read_json, read_model, write_json
from km_quantum.experiments.m2_benchmark import M2BenchmarkResult, build_m2_benchmark
from km_quantum.qubo.cli import run_acceptance
from km_quantum.qubo.encoder import encode_qubo
from km_quantum.qubo.ising import IsingModel, bits_to_spins
from km_quantum.qubo.models import QUBOConfig, QUBOModel
from km_quantum.qubo.references import infeasible_case, penalty_sensitivity_case
from km_quantum.qubo.verification import verify_equivalence

REPOSITORY = Path(__file__).resolve().parents[1]


def test_qubo_cli_complete_mini_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance_path = REPOSITORY / "data/synthetic/m2/mini_example.json"
    config_path = REPOSITORY / "configs/m2_mini_example.json"
    model_path, duplicate = tmp_path / "qubo.json", tmp_path / "repeat.json"
    solved_path, ising_path = tmp_path / "ground.json", tmp_path / "ising.json"
    verified_path, benchmark_path = tmp_path / "verified.json", tmp_path / "runs.jsonl"
    for output in (model_path, duplicate):
        assert (
            main(
                [
                    "qubo",
                    "encode",
                    "--instance",
                    str(instance_path),
                    "--config",
                    str(config_path),
                    "--output",
                    str(output),
                ]
            )
            == 0
        )
    assert model_path.read_bytes() == duplicate.read_bytes()
    assert main(["qubo", "inspect", "--model", str(model_path), "--matrix"]) == 0
    assert (
        main(
            [
                "qubo",
                "solve-exhaustive",
                "--instance",
                str(instance_path),
                "--model",
                str(model_path),
                "--output",
                str(solved_path),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "qubo",
                "verify",
                "--instance",
                str(instance_path),
                "--model",
                str(model_path),
                "--output",
                str(verified_path),
                "--benchmark-output",
                str(benchmark_path),
                "--experiment-id",
                "mini",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "qubo",
                "to-ising",
                "--model",
                str(model_path),
                "--output",
                str(ising_path),
            ]
        )
        == 0
    )
    outputs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert outputs[2]["matrix"] == [
        [-19, 0, 42, 0],
        [0, -18, 0, 42],
        [0, 0, -11, 0],
        [0, 0, 0, -11],
    ]
    solved = read_json(solved_path)
    assert solved["ground_energy"] == 5
    assert solved["ground_state_degeneracy"] == 1
    assert solved["ground_states"][0]["bits"] == "1100"
    assert solved["best_invalid_energy"] == 23 and solved["invalid_energy_margin"] == 18
    assert solved["first_excited_gap"] == 7
    assert read_json(verified_path)["status"] == "PASS"
    record = M2BenchmarkResult.model_validate_json(benchmark_path.read_text(encoding="utf-8"))
    instance = read_model(instance_path, SchedulingInstance)
    qubo = read_model(model_path, QUBOModel)
    assert record.schema_version == 2 and record.artifact_type == "m2_equivalence"
    assert record.instance_hash == canonical_hash(instance)
    assert record.qubo_hash == canonical_hash(qubo)
    assert record.ground_state_count == record.valid_ground_state_count == 1
    assert record.invalid_ground_state_count == 0
    assert record.best_valid_energy == 5
    assert record.classical_objective == record.decoded_qubo_objective == record.ground_energy == 5
    assert record.coefficient_dynamic_range == "42/11"
    assert record.environment["python"] and record.environment["ortools"]
    ising = read_model(ising_path, IsingModel)
    for bits in product((0, 1), repeat=4):
        assert ising.energy4(bits_to_spins(bits)) == 4 * qubo.energy(bits)


def test_unsafe_verification_is_saved_with_all_invalid_minima(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance, config = penalty_sensitivity_case()
    instance_path, config_path = tmp_path / "input.json", tmp_path / "config.json"
    output, records = tmp_path / "failed.json", tmp_path / "failed.jsonl"
    write_json(instance_path, instance)
    write_json(config_path, config)
    assert (
        main(
            [
                "qubo",
                "verify",
                "--instance",
                str(instance_path),
                "--config",
                str(config_path),
                "--output",
                str(output),
                "--benchmark-output",
                str(records),
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "FAIL" and not result["equivalence_passed"]
    record = M2BenchmarkResult.model_validate_json(records.read_text(encoding="utf-8"))
    assert record.invalid_ground_state_count > 0
    assert record.classical_objective == 100 and record.ground_energy == 26
    assert record.best_valid_energy == 100
    assert not record.penalty_configuration["safety_guaranteed"]
    assert record.ising_equivalence_passed


def test_original_infeasibility_is_honest_in_m2_records() -> None:
    instance, config = infeasible_case()
    model = encode_qubo(instance, config)
    verification = verify_equivalence(instance, model=model)
    record = build_m2_benchmark(instance, model, verification, experiment_id="infeasible")
    assert record.status == "INFEASIBLE" and not record.equivalence_passed
    assert record.classical_objective is None and record.decoded_qubo_objective is None
    assert record.best_valid_energy is None
    assert record.invalid_ground_state_count == record.ground_state_count
    assert record.ising_equivalence_passed


def test_qubo_cli_state_guard_is_structured_and_writes_no_partial_proof(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    instance_path = REPOSITORY / "data/synthetic/m2/mini_example.json"
    output = tmp_path / "limited.json"
    assert (
        main(
            [
                "qubo",
                "verify",
                "--instance",
                str(instance_path),
                "--max-states",
                "8",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)["error"]["code"] == "input_error"


def test_qubo_cli_rejects_mismatched_model_instance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = read_model(REPOSITORY / "data/synthetic/m2/mini_example.json", SchedulingInstance)
    model_path, wrong_path = tmp_path / "qubo.json", tmp_path / "wrong.json"
    write_json(model_path, encode_qubo(original, QUBOConfig()))
    write_json(wrong_path, original.model_copy(update={"instance_id": "different"}))
    assert (
        main(
            [
                "qubo",
                "verify",
                "--instance",
                str(wrong_path),
                "--model",
                str(model_path),
            ]
        )
        == 2
    )
    assert "canonical" in json.loads(capsys.readouterr().err)["error"]["message"]


def test_full_acceptance_gate_checks_safe_infeasible_and_sensitivity_cases() -> None:
    report = run_acceptance()
    assert report["status"] == report["m2_equivalence"] == "PASS"
    assert report["hand_case_count"] >= 12 and report["random_case_count"] == 100
    assert report["successful_feasible_cases"] == report["feasible_suite_case_count"]
    assert report["infeasible_status"] == "INFEASIBLE"
    assert report["enumerated_bitstrings"] > 100
    assert all(row["expected_outcome_observed"] for row in report["penalty_sensitivity"])
    assert [row["status"] for row in report["penalty_sensitivity"]] == [
        "FAIL",
        "FAIL",
        "PASS",
        "PASS",
        "PASS",
    ]
    assert all(row["ising_equivalence_passed"] for row in report["cases"])


def test_partial_random_suite_cannot_claim_acceptance_pass() -> None:
    with pytest.raises(ValueError, match="at least 100"):
        run_acceptance(random_cases=99)


def test_model_and_config_cannot_be_supplied_together(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exception:
        main(
            [
                "qubo",
                "verify",
                "--instance",
                "input.json",
                "--model",
                "model.json",
                "--config",
                "config.json",
            ]
        )
    assert exception.value.code == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "usage_error"


def test_benchmark_rejects_verification_paired_with_a_different_source() -> None:
    instance = read_model(REPOSITORY / "data/synthetic/m2/mini_example.json", SchedulingInstance)
    original_config = read_model(REPOSITORY / "configs/m2_mini_example.json", QUBOConfig)
    original_model = encode_qubo(instance, original_config)
    verified = verify_equivalence(instance, model=original_model)
    wrong_instance = instance.model_copy(update={"instance_id": "another-instance"})
    with pytest.raises(ValueError, match="supplied instance"):
        build_m2_benchmark(wrong_instance, original_model, verified, experiment_id="wrong-source")
    new_data = original_config.model_dump()
    new_data["objective"]["cost_weight"] = 2
    other_model = encode_qubo(instance, QUBOConfig.model_validate(new_data))
    with pytest.raises(ValueError, match="Verification Ising model"):
        build_m2_benchmark(instance, other_model, verified, experiment_id="wrong-result")
