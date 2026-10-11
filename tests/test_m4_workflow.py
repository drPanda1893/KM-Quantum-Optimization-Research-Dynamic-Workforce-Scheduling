"""Public M4 routes, real tiny ideal/noise execution, and refusal of incomplete evidence."""

import copy
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from km_quantum.__main__ import main
from km_quantum.m4 import acceptance, audit, benchmark, verification
from km_quantum.m4 import manifest as manifest_module
from km_quantum.m4.io import (
    canonical_hash,
    execution_environment,
    lf_file_hash,
    read_json,
    write_json,
)
from km_quantum.m4.models import M4Config
from km_quantum.m4.noise import DEFAULT_NOISE_PROFILES, CompilerConfig
from km_quantum.qaoa.references import load_references

ROOT = Path(__file__).resolve().parents[1]
REAL_VALIDATE_MANIFEST = manifest_module.validate_manifest


@pytest.fixture
def workflow_suite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    reference = load_references(ROOT)["A_single_job"]
    references = {reference.name: reference}
    config = M4Config(depths=(1,), restart_seeds=(0,), optimizer_maxiter=8)
    entry = {
        "reference_id": reference.name,
        "reference_hash": reference.reference_hash,
        "qubo_hash": reference.qubo_hash,
        "ising_hash": reference.ising_hash,
        "qubit_count": reference.qubo.number_variables,
        "group": "known_diagnostic",
        "split": "development",
        "cp_sat_reference": {"status": "OPTIMAL", "objective_value": reference.ground_energy},
    }
    training, noise, controls = manifest_module.expected_plan_rows([entry], config)
    selection_relative = "data/synthetic/m4/selection_audit.json"
    write_json(
        tmp_path / selection_relative,
        {
            "selections": [
                {
                    "status": "SELECTED",
                    "reference_id": reference.name,
                    "already_observed_in_m3": True,
                    "split": entry["split"],
                    "cp_sat_reference": entry["cp_sat_reference"],
                }
            ]
        },
    )
    manifest: dict[str, Any] = {
        "m3_reference_commit": "a" * 40,
        "config_hash": canonical_hash(config.model_dump(mode="json")),
        "configuration": config.model_dump(mode="json"),
        "references": [entry],
        "planned_training_runs": training,
        "planned_noise_runs": noise,
        "planned_control_runs": controls,
        "initial_resource_preflight": {"passed": True},
        "noise_profiles": [asdict(profile) for profile in DEFAULT_NOISE_PROFILES],
        "compiler": asdict(CompilerConfig(seed=config.transpiler_seed)),
        "inputs": {selection_relative: lf_file_hash(tmp_path / selection_relative)},
        "counts": {
            "instances": 1,
            "training": 2,
            "density": 10,
            "controls": 3,
            "preparations": 1,
            "cp_sat_references": 1,
        },
    }
    manifest["manifest_content_sha256"] = canonical_hash(manifest)
    integrity = {
        "unchanged": True,
        "protected_file_count": 2107,
        "protected_bytes_unchanged": True,
        "changed": [],
    }
    environment = {**execution_environment(ROOT), "git_commit": "a" * 40, "git_dirty": False}
    monkeypatch.setattr(manifest_module, "freeze_manifest", lambda root: manifest)
    monkeypatch.setattr(manifest_module, "load_config", lambda root: config)
    monkeypatch.setattr(manifest_module, "validate_manifest", lambda root, cfg: manifest)
    monkeypatch.setattr(manifest_module, "load_m4_references", lambda root: references)
    monkeypatch.setattr(manifest_module, "verify_reference_integrity", lambda root: integrity)
    monkeypatch.setattr(benchmark, "load_config", lambda root: config)
    monkeypatch.setattr(benchmark, "validate_manifest", lambda root, cfg: manifest)
    monkeypatch.setattr(benchmark, "load_m4_references", lambda root: references)
    monkeypatch.setattr(benchmark, "execution_environment", lambda root: environment)
    monkeypatch.setattr(benchmark, "source_content_hash", lambda root: "workflow_source_identity")
    monkeypatch.setattr(audit, "load_m4_references", lambda root: references)
    monkeypatch.setattr(audit, "source_content_hash", lambda root: "workflow_source_identity")
    monkeypatch.setattr(audit, "verify_reference_integrity", lambda root: integrity)
    monkeypatch.setattr(acceptance, "validate_manifest", lambda root, cfg: manifest)
    monkeypatch.setattr(acceptance, "verify_reference_integrity", lambda root: integrity)

    def technical_gate(root: Path, output: Path, cfg: M4Config, manifest: dict) -> dict:
        result = {
            "status": "PASS",
            "config_hash": manifest["config_hash"],
            "manifest_content_sha256": manifest["manifest_content_sha256"],
            **{
                name: True
                for name in (
                    "metric_semantics_verified",
                    "operator_equivalence",
                    "warm_start_mixer_verified",
                    "zero_noise_equivalence_verified",
                    "readout_model_verified",
                )
            },
        }
        write_json(output / "verification.json", result)
        return result

    # Full correctness gates have dedicated independent tests. This fixture still executes
    # actual A standard/warm optimization, compilation, all five channels and 1024-shot counts.
    monkeypatch.setattr(verification, "verify_m4", technical_gate)
    write_json(tmp_path / "data/synthetic/m4/benchmark_manifest.json", manifest)
    return {
        "root": tmp_path,
        "manifest": manifest,
        "config": config,
        "environment": environment,
        "output": tmp_path / "results/m4-cli",
    }


def _arguments(suite: dict[str, Any], command: str, *extra: str) -> list[str]:
    return [
        "m4",
        command,
        "--root",
        str(suite["root"]),
        "--output-directory",
        str(suite["output"]),
        *extra,
    ]


def test_public_manifest_verify_and_dry_run_paths(
    workflow_suite: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_arguments(workflow_suite, "manifest")) == 0
    frozen = json.loads(capsys.readouterr().out)
    assert frozen["status"] == "FROZEN" and frozen["counts"]["density"] == 10
    assert main(_arguments(workflow_suite, "verify")) == 0
    gate = json.loads(capsys.readouterr().out)
    assert gate["status"] == "PASS" and gate["readout_model_verified"]
    workflow_suite["output"] = workflow_suite["root"] / "results/m4-preview"
    assert main(_arguments(workflow_suite, "benchmark", "--dry-run")) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "DRY_RUN"
    assert (preview["training"], preview["density"], preview["controls"]) == (2, 10, 3)
    assert not workflow_suite["output"].exists()


def test_public_real_smoke_resume_and_summarize_preserve_original_observations(
    workflow_suite: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(_arguments(workflow_suite, "benchmark", "--smoke")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "COMPLETE" and result["scope"] == "technical_smoke"
    output = workflow_suite["output"]
    protected = {
        path.relative_to(output).as_posix(): lf_file_hash(path)
        for folder in ("preparations", "training", "compiled", "records", "controls", "traces")
        for path in (output / folder).iterdir()
    }
    assert len(list((output / "records").glob("*.json"))) == 10
    for path in (output / "records").glob("*.json"):
        assert sum(read_json(path)["sampling"]["counts"].values()) == 1024

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Resume must never repeat training or the original channel experiment")

    monkeypatch.setattr(benchmark, "train_variant", forbidden)
    monkeypatch.setattr(benchmark, "evaluate_noise", forbidden)
    assert main(_arguments(workflow_suite, "benchmark", "--smoke", "--resume")) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["status"] == "COMPLETE" and resumed["completed_executions"] == 10
    for relative, expected in protected.items():
        assert lf_file_hash(output / relative) == expected
    assert main(_arguments(workflow_suite, "summarize")) == 0
    summary = json.loads(capsys.readouterr().out)
    assert len(summary["plots"]) == 5
    assert all(Path(path).stat().st_size > 0 for path in summary["plots"])


def test_public_smoke_acceptance_stays_incomplete_even_with_successful_tooling(
    workflow_suite: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_arguments(workflow_suite, "benchmark", "--smoke")) == 0
    capsys.readouterr()
    assert main(_arguments(workflow_suite, "summarize")) == 0
    capsys.readouterr()
    report = workflow_suite["root"] / "docs/m4_completion_report.md"
    report.parent.mkdir(parents=True)
    report.write_text("Tiny fixture report\n", encoding="utf-8")
    tooling = workflow_suite["root"] / "tooling.json"
    write_json(
        tooling,
        {name: {"status": "PASS"} for name in ("pytest", "ruff", "format", "mypy", "build")},
    )
    assert main(_arguments(workflow_suite, "acceptance", "--tooling", str(tooling))) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INCOMPLETE"
    assert result["implementation_status"] == "PASS"
    assert result["experiment_status"]["scope"] == "technical_smoke"
    assert result["experiment_status"]["status"] == "INCOMPLETE"
    assert result["hardware_used"] is False and result["training_noise_used"] is False


def test_primary_cli_refuses_dirty_code_without_starting_training(
    workflow_suite: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        benchmark,
        "execution_environment",
        lambda root: {**workflow_suite["environment"], "git_dirty": True},
    )
    assert main(_arguments(workflow_suite, "benchmark")) == 2
    error = json.loads(capsys.readouterr().err)
    assert "clean code commit" in error["error"]["message"]
    assert not (workflow_suite["output"] / "execution_context.json").exists()
    assert not (workflow_suite["output"] / "training").exists()


def test_failed_correctness_gate_returns_visible_blocked_status(
    workflow_suite: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verification,
        "verify_m4",
        lambda *args, **kwargs: {"status": "FAIL", "errors": ["Fixture physicality failure"]},
    )
    assert main(_arguments(workflow_suite, "benchmark", "--smoke")) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED_GATE"
    assert "physicality" in result["verification"]["errors"][0]
    assert not (workflow_suite["output"] / "training").exists()


@pytest.mark.parametrize("changed", ["source", "dependencies", "plan"])
def test_resume_rejects_conflicting_code_stack_or_plan_before_new_work(
    workflow_suite: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    assert main(_arguments(workflow_suite, "benchmark", "--smoke")) == 0
    capsys.readouterr()
    if changed == "source":
        monkeypatch.setattr(benchmark, "source_content_hash", lambda root: "changed_source")
    elif changed == "dependencies":
        monkeypatch.setattr(
            benchmark,
            "execution_environment",
            lambda root: {**workflow_suite["environment"], "dependencies": {"qiskit": "wrong"}},
        )
    else:
        workflow_suite["manifest"]["manifest_content_sha256"] = "changed_manifest"
    assert main(_arguments(workflow_suite, "benchmark", "--smoke", "--resume")) == 2
    error = json.loads(capsys.readouterr().err)
    assert "Resume" in error["error"]["message"] and "mismatch" in error["error"]["message"]
    assert len(list((workflow_suite["output"] / "records").glob("*.json"))) == 10


def test_real_manifest_validation_rejects_config_or_content_conflict(
    workflow_suite: dict[str, Any],
) -> None:
    root, config = workflow_suite["root"], workflow_suite["config"]
    assert REAL_VALIDATE_MANIFEST(root, config)["counts"]["training"] == 2
    with pytest.raises(ValueError, match="configuration differs"):
        REAL_VALIDATE_MANIFEST(root, config.model_copy(update={"epsilon": 0.5}))
    path = root / "data/synthetic/m4/benchmark_manifest.json"
    tampered = copy.deepcopy(read_json(path))
    tampered["references"][0]["qubit_count"] += 1
    write_json(path, tampered)
    with pytest.raises(ValueError, match="hash mismatch"):
        REAL_VALIDATE_MANIFEST(root, config)


@pytest.mark.parametrize("output", ["results/m3", "docs/m4", "../m4", "results", "."])
def test_public_cli_output_boundaries_are_structured_errors(
    workflow_suite: dict[str, Any], capsys: pytest.CaptureFixture[str], output: str
) -> None:
    assert (
        main(
            [
                "m4",
                "benchmark",
                "--root",
                str(workflow_suite["root"]),
                "--output-directory",
                output,
                "--dry-run",
            ]
        )
        == 2
    )
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "input_error"
    assert "dedicated results/m4" in error["error"]["message"]


def test_primary_manifest_resource_overrun_cannot_be_a_dry_run_success(
    workflow_suite: dict[str, Any],
) -> None:
    manifest = copy.deepcopy(workflow_suite["manifest"])
    # Re-sealing an oversized plan does not bypass the fixed run/instance limits.
    manifest["references"] *= 13
    training, noise, controls = manifest_module.expected_plan_rows(
        manifest["references"], workflow_suite["config"]
    )
    manifest["planned_training_runs"] = training
    manifest["planned_noise_runs"] = noise
    manifest["planned_control_runs"] = controls
    manifest["counts"]["instances"] = manifest["counts"]["preparations"] = 13
    manifest["counts"]["cp_sat_references"] = 13
    manifest["counts"]["training"] = len(training)
    manifest["counts"]["density"] = len(noise)
    manifest["counts"]["controls"] = len(controls)
    manifest["manifest_content_sha256"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_content_sha256"}
    )
    write_json(workflow_suite["root"] / "data/synthetic/m4/benchmark_manifest.json", manifest)
    with pytest.raises(ValueError, match="instance_count exceeds"):
        REAL_VALIDATE_MANIFEST(workflow_suite["root"], workflow_suite["config"])


@pytest.mark.parametrize("changed", ["per_run_noise", "restart_removed"])
def test_resealed_plan_cannot_change_noise_strength_or_remove_fixed_training(
    workflow_suite: dict[str, Any], changed: str
) -> None:
    manifest = copy.deepcopy(workflow_suite["manifest"])
    if changed == "per_run_noise":
        manifest["planned_noise_runs"][1]["noise_profile"]["lambda_1q"] = 0.1
        expected_error = "planned_noise_runs"
    else:
        removed = manifest["planned_training_runs"].pop()
        manifest["planned_noise_runs"] = [
            row
            for row in manifest["planned_noise_runs"]
            if row["training_id"] != removed["training_id"]
        ]
        manifest["counts"]["training"] = len(manifest["planned_training_runs"])
        manifest["counts"]["density"] = len(manifest["planned_noise_runs"])
        expected_error = "planned_training_runs"
    manifest["manifest_content_sha256"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_content_sha256"}
    )
    write_json(workflow_suite["root"] / "data/synthetic/m4/benchmark_manifest.json", manifest)
    with pytest.raises(ValueError, match=expected_error):
        REAL_VALIDATE_MANIFEST(workflow_suite["root"], workflow_suite["config"])


def test_outer_wall_timeout_is_recorded_with_all_remaining_executions_visible(
    workflow_suite: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def clock() -> float:
        nonlocal calls
        calls += 1
        return 0.0 if calls == 1 else 8000.0

    monkeypatch.setattr(benchmark, "perf_counter", clock)
    assert main(_arguments(workflow_suite, "benchmark", "--smoke")) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INCOMPLETE" and result["stopped_at_wall_limit"]
    output = workflow_suite["output"]
    ledger = [
        json.loads(line)
        for line in (output / "execution_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(ledger) == 10
    assert all(row["status"] == "SKIPPED_WALL_LIMIT" for row in ledger)
    assert read_json(output / "benchmark_execution.json")["status"] == "INCOMPLETE"


def test_classical_preparation_exception_is_visible_and_preserves_every_planned_row(
    workflow_suite: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failed_preparation(*args: object, **kwargs: object) -> None:
        raise ValueError("Deliberate preparation fixture failure")

    monkeypatch.setattr(benchmark, "solve_box_relaxation", failed_preparation)
    assert main(_arguments(workflow_suite, "benchmark", "--smoke")) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INCOMPLETE" and result["failures"]
    assert "preparation fixture failure" in str(result["failures"])
    output = workflow_suite["output"]
    ledger = [
        json.loads(line)
        for line in (output / "execution_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(ledger) == 10 and all(row["status"] != "COMPLETE" for row in ledger)
    assert not list((output / "traces").glob("*.jsonl"))
    assert read_json(output / "benchmark_execution.json")["status"] == "INCOMPLETE"


@pytest.mark.parametrize("defect", ["false_technical_flag", "wrong_plot_names"])
def test_acceptance_rejects_missing_named_plots_or_false_gate_inside_pass_report(
    workflow_suite: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    defect: str,
) -> None:
    assert main(_arguments(workflow_suite, "benchmark")) == 0
    capsys.readouterr()
    output = workflow_suite["output"]
    report = workflow_suite["root"] / "docs/m4_completion_report.md"
    report.parent.mkdir(parents=True)
    report.write_text("Primary A fixture report\n", encoding="utf-8")
    plots = output / "plots"
    plots.mkdir()
    for name in acceptance.PLOT_NAMES:
        (plots / name).write_bytes(b"nonempty plot fixture")
    tooling = workflow_suite["root"] / "tooling.json"
    write_json(
        tooling,
        {name: {"status": "PASS"} for name in ("pytest", "ruff", "format", "mypy", "build")},
    )
    if defect == "false_technical_flag":
        gate = read_json(output / "verification.json")
        gate["readout_model_verified"] = False
        assert gate["status"] == "PASS"
        write_json(output / "verification.json", gate)
    else:
        (plots / acceptance.PLOT_NAMES[0]).rename(plots / "unrelated.png")
        assert len(list(plots.glob("*.png"))) == 5
    assert main(_arguments(workflow_suite, "acceptance", "--tooling", str(tooling))) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["artifact_audit"]["status"] == "PASS"
    assert result["experiment_status"]["status"] == "COMPLETE"
    assert result["status"] == "INCOMPLETE"
    if defect == "false_technical_flag":
        assert not result["readout_model_verified"]
    else:
        assert not result["required_plots_complete"]
