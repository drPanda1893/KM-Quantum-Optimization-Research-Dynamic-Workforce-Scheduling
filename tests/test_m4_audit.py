"""Saved observations cannot pass audit after semantic or integrity tampering."""

import copy
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from km_quantum.m4 import audit, benchmark, verification
from km_quantum.m4.audit import append_seal, audit_experiment, read_seals, verify_seal
from km_quantum.m4.io import canonical_hash, read_json, write_json
from km_quantum.m4.models import M4Config
from km_quantum.m4.noise import DEFAULT_NOISE_PROFILES
from km_quantum.qaoa.references import load_references

ROOT = Path(__file__).resolve().parents[1]


def _seal_files(output: Path, *, replace: bool = False) -> None:
    index = output / "artifact_index.jsonl"
    if replace and index.exists():
        index.unlink()
    for directory in ("preparations", "controls", "training", "compiled", "records"):
        for path in sorted((output / directory).glob("*.json")):
            append_seal(output, path.relative_to(output), read_json(path))


@pytest.fixture
def campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, Any], M4Config]:
    reference = load_references(ROOT)["A_single_job"]
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
    training, noise, controls = [], [], []
    for algorithm in ("standard", "warm_start"):
        common = {
            "training_id": f"A_single_job-{algorithm}-p1-seed0",
            "reference_id": reference.name,
            "algorithm": algorithm,
            "p": 1,
            "initialization_seed": 0,
        }
        training.append(common)
        for index, profile in enumerate(DEFAULT_NOISE_PROFILES):
            noise.append(
                {
                    **common,
                    "run_id": f"{common['training_id']}-{profile.name}",
                    "noise_profile": asdict(profile),
                    "sampling_seed": 4120000 + index,
                }
            )
    for index, control in enumerate(("uniform", "rounded_relaxation", "warm_product")):
        controls.append(
            {
                "control_id": f"A_single_job-{control}",
                "reference_id": reference.name,
                "control": control,
                "sampling_seed": 7150000 + index,
            }
        )
    manifest = {
        "config_hash": canonical_hash(config.model_dump(mode="json")),
        "configuration": config.model_dump(mode="json"),
        "references": [entry],
        "planned_training_runs": training,
        "planned_noise_runs": noise,
        "planned_control_runs": controls,
        "initial_resource_preflight": {"passed": True},
    }
    manifest["manifest_content_sha256"] = canonical_hash(manifest)
    monkeypatch.setattr(benchmark, "load_config", lambda root: config)
    monkeypatch.setattr(benchmark, "validate_manifest", lambda root, cfg: manifest)
    monkeypatch.setattr(benchmark, "load_m4_references", lambda root: {reference.name: reference})
    monkeypatch.setattr(audit, "load_m4_references", lambda root: {reference.name: reference})
    monkeypatch.setattr(
        benchmark,
        "execution_environment",
        lambda root: {"git_commit": "a" * 40, "git_dirty": False},
    )
    monkeypatch.setattr(benchmark, "source_content_hash", lambda root: "test_source_identity")
    monkeypatch.setattr(audit, "source_content_hash", lambda root: "test_source_identity")
    monkeypatch.setattr(
        audit,
        "verify_reference_integrity",
        lambda root: {"unchanged": True, "protected_file_count": 2107},
    )
    monkeypatch.setattr(verification, "verify_m4", lambda *args, **kwargs: {"status": "PASS"})
    output = tmp_path / "results/m4-fixture"
    result = benchmark.run_benchmark(tmp_path, output)
    assert result["status"] == "COMPLETE", result
    _seal_files(output)
    return output, manifest, config


def test_seals_are_portable_idempotent_and_reject_replacement(tmp_path: Path) -> None:
    row = {"angle": [0.1, 0.3], "status": "COMPLETE"}
    first = append_seal(tmp_path, "training/example.json", row)
    assert first == append_seal(tmp_path, Path("training/example.json"), copy.deepcopy(row))
    assert len((tmp_path / "artifact_index.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    verify_seal(tmp_path, "training/example.json", row)
    with pytest.raises(ValueError, match="replace"):
        append_seal(tmp_path, "training/example.json", {**row, "angle": [0.9]})
    with pytest.raises(ValueError, match="does not match"):
        verify_seal(tmp_path, "training/example.json", {**row, "status": "FAILED"})
    with pytest.raises(ValueError, match="relative"):
        append_seal(tmp_path, "../m3/old.json", row)
    with (tmp_path / "artifact_index.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(first) + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        read_seals(tmp_path)


def test_real_tiny_campaign_audits_all_layers_without_retraining_or_noisy_resimulation(
    campaign: tuple[Path, dict[str, Any], M4Config],
    tmp_path: Path,
) -> None:
    output, manifest, config = campaign
    result = audit_experiment(tmp_path, output, manifest, config)
    assert result["status"] == "PASS", result
    assert result["checked"] == {
        "preparations": 1,
        "controls": 3,
        "training": 2,
        "compiled": 2,
        "density": 10,
    }
    assert result["missing"] == result["failed"] == result["errors"] == []
    assert result["optimization_performed"] is False
    assert result["primary_density_resimulation_performed"] is False
    assert read_json(output / "audit.json") == result


@pytest.mark.parametrize(
    "changed",
    [
        "probabilities",
        "metrics",
        "sample",
        "compiler",
        "preparation",
        "initial_energy",
        "physicality",
        "tv",
        "readout_only",
        "dirty",
        "training_summary",
        "timings",
        "distribution_timing",
    ],
)
def test_even_resealed_forged_results_fail_independent_semantic_checks(
    campaign: tuple[Path, dict[str, Any], M4Config],
    tmp_path: Path,
    changed: str,
) -> None:
    output, manifest, config = campaign
    record_path = output / "records" / f"{manifest['planned_noise_runs'][0]['run_id']}.json"
    if changed == "preparation":
        path = output / "preparations/A_single_job.json"
        row = read_json(path)
        row["relaxation"]["lambda"] += 1
    elif changed == "initial_energy":
        path = output / "training" / f"{manifest['planned_training_runs'][0]['training_id']}.json"
        row = read_json(path)
        row["initial_expected_energy"] += 1
    elif changed == "compiler":
        path = output / "compiled" / f"{manifest['planned_training_runs'][0]['training_id']}.json"
        row = read_json(path)
        row["depth"] += 1
    elif changed == "dirty":
        path = output / "execution_context.json"
        row = read_json(path)
        row["environment"]["git_dirty"] = True
    else:
        path = record_path
        row = read_json(path)
        if changed == "probabilities":
            row["probabilities"]["before_readout"] = [0.25] * 4
        elif changed == "metrics":
            row["metrics"]["after_readout"]["p_schedule_optimal"] = 0.123
        elif changed == "sample":
            counts = row["sampling"]["counts"]
            key = next(iter(counts))
            counts[key] += 1
        elif changed == "physicality":
            row["noise_metadata"]["density_physicality"]["minimum_eigenvalue"] = -0.1
        elif changed == "tv":
            row["noise_metadata"]["total_variation_distance_ideal_to_observed"] += 0.1
        elif changed == "readout_only":
            row["noise_metadata"]["readout_applications"] = 2
        elif changed == "training_summary":
            row["training"]["nfev"] += 1
        elif changed == "timings":
            row["timings"]["standalone_end_to_end_ms"] += 100
        elif changed == "distribution_timing":
            row["timings"]["distribution_analysis_runtime_ms"] = -1
    write_json(path, row)
    _seal_files(output, replace=True)
    result = audit_experiment(tmp_path, output, manifest, config)
    assert result["status"] == "INVALID", result
    assert result["errors"]


def test_changed_artifact_without_reseal_fails_integrity_first(
    campaign: tuple[Path, dict[str, Any], M4Config],
    tmp_path: Path,
) -> None:
    output, manifest, config = campaign
    path = output / "controls/A_single_job-uniform.json"
    row = read_json(path)
    row["metrics"]["p_schedule_optimal"] = 0.123
    write_json(path, row)
    result = audit_experiment(tmp_path, output, manifest, config)
    assert result["status"] == "INVALID"
    assert "seal mismatch" in result["errors"][0]


def test_missing_trace_remains_invalid_and_visible(
    campaign: tuple[Path, dict[str, Any], M4Config],
    tmp_path: Path,
) -> None:
    output, manifest, config = campaign
    trace = next((output / "traces").glob("*.jsonl"))
    trace.unlink()
    result = audit_experiment(tmp_path, output, manifest, config)
    assert result["status"] == "INVALID"
    assert result["errors"]


def test_unexecuted_planned_records_remain_incomplete(
    campaign: tuple[Path, dict[str, Any], M4Config],
    tmp_path: Path,
) -> None:
    output, manifest, config = campaign
    path = output / "records" / f"{manifest['planned_noise_runs'][-1]['run_id']}.json"
    path.unlink()
    _seal_files(output, replace=True)
    benchmark._record_jsonl(
        output / "runs.jsonl",
        [read_json(item) for item in sorted((output / "records").glob("*.json"))],
    )
    result = audit_experiment(tmp_path, output, manifest, config)
    assert result["status"] == "INCOMPLETE", result
    assert result["missing"] == [path.relative_to(output).as_posix()]
    assert result["checked"]["density"] == 9


def test_duplicate_artifact_index_entry_is_invalid(
    campaign: tuple[Path, dict[str, Any], M4Config],
    tmp_path: Path,
) -> None:
    output, manifest, config = campaign
    index = output / "artifact_index.jsonl"
    first = index.read_text(encoding="utf-8").splitlines()[0]
    with index.open("a", encoding="utf-8") as stream:
        stream.write(first + "\n")
    result = audit_experiment(tmp_path, output, manifest, config)
    assert result["status"] == "INVALID"
    assert "duplicate" in result["errors"][0]


def test_changed_aggregate_export_cannot_override_original_records(
    campaign: tuple[Path, dict[str, Any], M4Config],
    tmp_path: Path,
) -> None:
    output, manifest, config = campaign
    path = output / "runs.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["metrics"]["after_readout"]["p_schedule_optimal"] = 0.123
    benchmark._record_jsonl(path, rows)
    result = audit_experiment(tmp_path, output, manifest, config)
    assert result["status"] == "INVALID"
    assert "aggregate artifact differs" in result["errors"][0]
