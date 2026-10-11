"""Prespecification, protected-reference drift and complete restart statistics."""

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from km_quantum.qaoa.benchmark import (
    MANIFEST_PATH,
    PRIMARY_CONFIG,
    aggregate_runs,
    build_acceptance,
    create_manifest,
    create_plots,
    exploratory_correlations,
    run_benchmark,
    verify_provenance,
)

ROOT = Path(__file__).resolve().parents[1]


def _study_root(tmp_path: Path) -> Path:
    """Copy only the immutable inputs needed for manifest generation."""
    for relative in (
        "data/synthetic/m2/frozen_qubos.jsonl",
        "data/synthetic/m3/provenance.json",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    provenance_path = tmp_path / "data/synthetic/m3/provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["protected_files_sha256"] = {
        "data/synthetic/m2/frozen_qubos.jsonl": provenance["protected_files_sha256"][
            "data/synthetic/m2/frozen_qubos.jsonl"
        ]
    }
    provenance.pop("reconstructed_acceptance_report_sha256", None)
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    return tmp_path


def _run(seed: int, energy: float, probability: float, success: bool) -> dict[str, object]:
    return {
        "instance_id": "example",
        "p": 1,
        "initialization_seed": seed,
        "qubit_count": 2,
        "final_expected_energy": energy,
        "normalized_energy_error": energy / 10,
        "ground_state_probability": probability,
        "valid_probability": 0.8,
        "uniform_ground_probability": 0.25,
        "runtime_ms": float(seed + 1),
        "nfev": 300 if not success else 50,
        "success": success,
    }


def test_manifest_is_frozen_deterministic_and_structurally_selected(tmp_path: Path) -> None:
    root = _study_root(tmp_path)
    manifest = create_manifest(root)
    assert create_manifest(root) == manifest
    assert (root / MANIFEST_PATH).is_file()
    assert manifest["reference_count"] == 114
    assert manifest["eligible_reference_count"] == 113
    assert manifest["primary_reference_count"] == 22
    assert manifest["planned_primary_runs"] == 660
    assert manifest["configuration"] == PRIMARY_CONFIG
    assert manifest["configuration"]["restart_seeds"] == list(range(10))
    excluded = manifest["excluded_references"]
    assert len(excluded) == 1
    assert excluded[0]["reference_id"] == "O_capacity_and_imbalance"
    assert excluded[0]["qubit_count"] == 15
    selected = manifest["primary_suite"]
    assert sum(row["family"] == "hand" for row in selected) == 13
    random = [row for row in selected if row["family"] == "random"]
    assert sorted(row["qubit_count"] for row in random) == [1, 2, 3, 4, 5, 6, 7, 8, 10]
    eligible = selected + manifest["eligible_not_selected"]
    for row in selected:
        identifier_hash = hashlib.sha256(row["instance_id"].encode()).hexdigest()
        assert row["split"] == (
            "development" if int(identifier_hash, 16) % 5 == 0 else "evaluation"
        )
        assert row["ground_state_count"] in (1, 2)
        assert len(row["reference_sha256"]) == 64
        if row["family"] == "random":
            same_size = [
                entry
                for entry in eligible
                if entry["family"] == "random" and entry["qubit_count"] == row["qubit_count"]
            ]
            assert row["instance_id_sha256"] == min(
                entry["instance_id_sha256"] for entry in same_size
            )


def test_manifest_refuses_to_overwrite_changed_prespecification(tmp_path: Path) -> None:
    root = _study_root(tmp_path)
    create_manifest(root)
    path = root / MANIFEST_PATH
    original = json.loads(path.read_text(encoding="utf-8"))
    original["configuration"]["optimizer_config"]["maxiter"] = 1
    path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="already frozen"):
        create_manifest(root)


def test_lf_checkout_preserves_content_gate_and_frozen_manifest(tmp_path: Path) -> None:
    root = _study_root(tmp_path)
    frozen = root / "data/synthetic/m2/frozen_qubos.jsonl"
    # Simulate the initial Windows snapshot on every test platform.
    raw = frozen.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    frozen.write_bytes(raw)
    original = create_manifest(root)
    frozen.write_bytes(raw.replace(b"\r\n", b"\n"))
    verification = verify_provenance(root)
    assert verification["unchanged"]
    assert verification["mathematical_unchanged"]
    assert not verification["protected_bytes_unchanged"]
    assert len(verification["line_ending_only_changes"]) == 1
    assert verification["changed"] == []
    assert create_manifest(root) == original
    # LF equivalence never permits an actual content change.
    frozen.write_bytes(frozen.read_bytes().replace(b'"ground_energy":0', b'"ground_energy":1', 1))
    verification = verify_provenance(root)
    assert not verification["unchanged"]
    assert not verification["mathematical_unchanged"]
    assert len(verification["changed"]) == 1
    with pytest.raises(ValueError, match="artifacts changed"):
        create_manifest(root)


@pytest.mark.parametrize("remove", [False, True])
def test_provenance_detects_changed_or_missing_reference(tmp_path: Path, remove: bool) -> None:
    root = _study_root(tmp_path)
    assert verify_provenance(root)["unchanged"]
    frozen = root / "data/synthetic/m2/frozen_qubos.jsonl"
    if remove:
        frozen.unlink()
    else:
        frozen.write_bytes(frozen.read_bytes() + b"\n")
    verification = verify_provenance(root)
    assert not verification["unchanged"]
    assert verification["count"] == 1
    assert len(verification["changed"]) == 1
    with pytest.raises(ValueError, match="artifacts changed"):
        create_manifest(root)


def test_restart_summary_retains_budget_terminations_and_spread() -> None:
    summaries = aggregate_runs([_run(0, 1, 0.8, True), _run(1, 9, 0.2, False)])
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["number_runs"] == 2
    assert summary["mean_final_expected_energy"] == 5
    assert summary["median_final_expected_energy"] == 5
    assert summary["best_final_expected_energy"] == 1
    assert summary["worst_final_expected_energy"] == 9
    assert summary["standard_deviation_final_expected_energy"] == pytest.approx(32**0.5)
    assert summary["mean_ground_state_probability"] == 0.5
    assert summary["ground_probability_success_rate"] == 0.5
    assert summary["optimizer_convergence_rate"] == 0.5
    assert summary["distributions"]["nfev"]["mean"] == 175


def test_summary_rejects_duplicate_restart_and_nonfinite_metrics() -> None:
    run = _run(0, 1, 0.8, True)
    with pytest.raises(ValueError, match="duplicate restart"):
        aggregate_runs([run, run])
    run["final_expected_energy"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        aggregate_runs([run])


def test_exploratory_analysis_does_not_invent_constant_correlations() -> None:
    runs = []
    references = []
    for index in range(3):
        run = _run(0, 1, 0.8, True)
        run["instance_id"] = f"case-{index}"
        runs.append(run)
        references.append(
            {
                "instance_id": run["instance_id"],
                "split": "evaluation",
                "qubit_count": 2,
                "constraint_energy_margin": 1,
                "structural_metadata": {
                    "qubo_density": 1.0,
                    "coefficient_dynamic_range": "3/2",
                    "slack_variable_count": 0,
                    "assignment_variable_count": 1,
                    "preprocessing_reduction": 0,
                    "active_constraint_component_count": 1,
                },
            }
        )
    analysis = exploratory_correlations(aggregate_runs(runs), {"primary_suite": references})
    assert all(row["pearson_r"] is None for row in analysis["records"])
    assert "Small" in analysis["warning"]
    relevant = [
        row for row in analysis["records"] if row["split"] == "evaluation" and row["p"] == 1
    ]
    assert all(row["instance_count"] == 3 for row in relevant)


def test_all_five_plots_are_created_from_raw_saved_metrics(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"evaluation":1,"expected_energy":9}\n{"evaluation":2,"expected_energy":1}\n',
        encoding="utf-8",
    )
    paths = create_plots([_run(0, 1, 0.8, True)], [trace], tmp_path / "plots")
    assert len(paths) == 5
    assert all(Path(path).read_bytes().startswith(b"\x89PNG") for path in paths)


def test_quality_execution_is_blocked_by_missing_m2_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from km_quantum.qaoa import references, simulation

    inputs = references.load_references(ROOT)
    monkeypatch.setattr(references, "load_references", lambda root: inputs)
    attempted = []
    monkeypatch.setattr(simulation, "run_qaoa", lambda *args, **kwargs: attempted.append(True))
    root = _study_root(tmp_path / "input")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="acceptance report is missing"):
        run_benchmark(root, output)
    assert not attempted
    failure = json.loads((output / "gate_failure.json").read_text(encoding="utf-8"))
    assert failure["passed"] is False
    assert failure["error_type"] == "ValueError"


def test_acceptance_cannot_pass_partial_or_absent_study_with_green_tooling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from km_quantum.qaoa import references

    inputs = references.load_references(ROOT)
    monkeypatch.setattr(references, "load_references", lambda root: inputs)
    root = _study_root(tmp_path / "input")
    report = build_acceptance(
        root,
        tmp_path / "output",
        tooling={name: True for name in ("pytest", "ruff", "format", "mypy", "build")},
    )
    assert report["status"] == "INCOMPLETE"
    assert report["tooling_passed"] is True
    assert report["planned_runs"] == 660
    assert len(report["missing_runs"]) == 660
    assert report["verified_runs"] == 0
    assert report["performance_is_acceptance_criterion"] is False
    assert report["sampling_verified"] is False
    assert report["operator_equivalence"] is False


def test_m2_acceptance_matches_every_original_oracle_objective(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from km_quantum.qaoa.benchmark import _verify_m2_acceptance
    from km_quantum.qaoa.references import load_references

    root = _study_root(tmp_path)
    report = json.loads((ROOT / "results/m2-acceptance.json").read_text(encoding="utf-8"))
    report["cases"][0]["classical_objective"] += 1
    path = root / "results/m2-acceptance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    provenance_path = root / "data/synthetic/m3/provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["reconstructed_acceptance_report_sha256"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match frozen reference"):
        _verify_m2_acceptance(root, load_references(ROOT))


def test_resume_refuses_changed_commit_or_config_before_loading_trace(tmp_path: Path) -> None:
    from km_quantum.qaoa.benchmark import _expected_config, _validate_saved_run
    from km_quantum.qaoa.references import load_references

    reference = load_references(ROOT)["F_unassigned_override"]
    manifest = {"manifest_content_sha256": "fixed"}
    run = {
        "status": "COMPLETE",
        "p": 1,
        "initialization_seed": 0,
        "instance_id": reference.instance.instance_id,
        "m2_reference_hash": reference.reference_hash,
        "qubo_hash": reference.qubo_hash,
        "ising_hash": reference.ising_hash,
        "qubit_count": reference.qubo.number_variables,
        "config": _expected_config(1, 0),
        "manifest_content_sha256": "fixed",
        "environment": {"git_commit": "old-commit"},
    }
    with pytest.raises(ValueError, match="environment differs: git_commit"):
        _validate_saved_run(run, reference, manifest, {"git_commit": "new-commit"}, tmp_path)
    run["config"]["maxiter"] = 301
    with pytest.raises(ValueError, match="configuration differs"):
        _validate_saved_run(run, reference, manifest, {"git_commit": "old-commit"}, tmp_path)


def test_separate_split_execution_preserves_combined_summaries_and_plots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stub quantum execution to exercise only persisted split orchestration."""
    from km_quantum.qaoa import benchmark, sampling, simulation

    entries = [
        {
            "reference_id": name,
            "instance_id": name,
            "split": split,
            "qubit_count": 2,
            "structural_metadata": {},
        }
        for name, split in (("dev-fixture", "development"), ("eval-fixture", "evaluation"))
    ]
    manifest = {"primary_suite": entries, "manifest_content_sha256": "fixed"}
    references = {
        entry["reference_id"]: SimpleNamespace(
            name=entry["reference_id"],
            instance=SimpleNamespace(instance_id=entry["instance_id"]),
        )
        for entry in entries
    }
    environment = {"git_dirty": False, "git_commit": "fixed-code"}
    monkeypatch.setattr(benchmark, "create_manifest", lambda root: manifest)
    monkeypatch.setattr(
        benchmark,
        "_full_gate",
        lambda root, output, manifest, progress: (
            {"environment": environment},
            references,
            dict.fromkeys(references),
        ),
    )
    validations = []
    monkeypatch.setattr(
        benchmark, "_validate_saved_run", lambda run, *args: validations.append(run["run_id"])
    )
    monkeypatch.setattr(benchmark, "build_acceptance", lambda *args: {})

    def fake_run(reference, config, *, trace_path, table):
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text("{}\n", encoding="utf-8")
        run = _run(config.initialization_seed, 1.0, 0.8, True)
        run.update(
            status="COMPLETE",
            instance_id=reference.instance.instance_id,
            p=config.p,
            run_id=f"{reference.name}-p{config.p}-seed{config.initialization_seed}",
        )
        return run, None, None

    monkeypatch.setattr(simulation, "run_qaoa", fake_run)
    monkeypatch.setattr(sampling, "sample_state", lambda *args, **kwargs: {"fixture": True})
    plot_inputs = []

    def fake_plots(runs, traces, output):
        plot_inputs.append((len(runs), len(traces), {run["split"] for run in runs}))
        return []

    monkeypatch.setattr(benchmark, "create_plots", fake_plots)
    output = tmp_path / "output"
    first = run_benchmark(tmp_path, output, split="development")
    previous_files = {
        path.name: path.read_bytes() for path in (output / "runs").glob("dev-fixture-*.json")
    }
    second = run_benchmark(tmp_path, output, split="evaluation")
    assert first["completed_runs"] == first["summary_run_count"] == 30
    assert second["completed_runs"] == 30
    assert second["summary_run_count"] == 60
    summaries = json.loads((output / "summaries/restarts.json").read_text())["summaries"]
    assert len(summaries) == 6
    assert all(row["number_runs"] == 10 for row in summaries)
    assert {row["split"] for row in summaries} == {"development", "evaluation"}
    assert plot_inputs == [
        (30, 30, {"development"}),
        (60, 60, {"development", "evaluation"}),
    ]
    assert previous_files == {
        path.name: path.read_bytes() for path in (output / "runs").glob("dev-fixture-*.json")
    }
    assert sum(name.startswith("dev-fixture-") for name in validations) == 90


@pytest.fixture
def saved_correctness_run(tmp_path: Path):
    """Build a tiny persisted correctness fixture without optimizer benchmarking."""
    import numpy as np
    from qiskit.quantum_info import Statevector

    from km_quantum.qaoa.analysis import analysis_for_probabilities, build_state_table
    from km_quantum.qaoa.benchmark import _expected_config
    from km_quantum.qaoa.references import load_references
    from km_quantum.qaoa.sampling import analyze_counts
    from km_quantum.qaoa.simulation import bind_parameters, build_qaoa

    reference = load_references(ROOT)["F_unassigned_override"]
    prepared = build_qaoa(reference.ising, 1)
    table = build_state_table(reference)
    initial = np.random.default_rng(0).uniform(0, np.pi, len(prepared.parameters)).tolist()
    final = [0.0] * len(prepared.parameters)
    final_probabilities = Statevector.from_instruction(
        bind_parameters(prepared, final)
    ).probabilities()
    energy = float(np.dot(final_probabilities, table.energies))
    analysis = analysis_for_probabilities(reference, table, final_probabilities, energy)
    initial_probabilities = Statevector.from_instruction(
        bind_parameters(prepared, initial)
    ).probabilities()
    initial_energy = float(np.dot(initial_probabilities, table.energies))
    sample = analyze_counts(
        reference,
        table,
        {"0": 512, "1": 512},
        shots=1024,
        seed=10100,
        exact_ground_probability=analysis["ground_state_probability"],
    )
    run_id = f"{reference.name}-p1-seed0"
    trace = tmp_path / "traces" / f"{run_id}.jsonl"
    trace.parent.mkdir(parents=True)
    rows = [
        {"evaluation": 1, "parameters": initial, "expected_energy": initial_energy},
        {
            "evaluation": 2,
            "parameters": final,
            "phase": "verification",
            "expected_energy": energy,
        },
    ]
    trace.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    sample_path = tmp_path / "samples" / f"{run_id}.json"
    sample_path.parent.mkdir(parents=True)
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    environment = {"git_commit": "correctness-fixture"}
    manifest = {"manifest_content_sha256": "fixed"}
    run = {
        "status": "COMPLETE",
        "p": 1,
        "initialization_seed": 0,
        "instance_id": reference.instance.instance_id,
        "m2_reference_hash": reference.reference_hash,
        "qubo_hash": reference.qubo_hash,
        "ising_hash": reference.ising_hash,
        "qubit_count": reference.qubo.number_variables,
        "ground_state_count": len(reference.ground_bits),
        "config": _expected_config(1, 0),
        "manifest_content_sha256": "fixed",
        "environment": environment,
        "parameter_order": [parameter.name for parameter in prepared.parameters],
        "parameter_count": len(prepared.parameters),
        "initial_parameters": initial,
        "final_parameters": final,
        "parameter_bindings": [
            {
                "position": index,
                "name": parameter.name,
                "initial_value": initial[index],
                "final_value": final[index],
            }
            for index, parameter in enumerate(prepared.parameters)
        ],
        "initial_expected_energy": initial_energy,
        "final_expected_energy": energy,
        "operator_equivalence": True,
        "nfev": 1,
        "total_expectation_evaluations": 2,
        "verification_evaluations": 1,
        "sampling": sample,
        "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
        "sample_sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
        **analysis,
    }
    return run, reference, manifest, environment, tmp_path


@pytest.mark.parametrize(
    ("metric", "forged_value"),
    [("valid_probability", 0.0), ("normalized_energy_error", -100.0), ("ground_state_count", 999)],
)
def test_resume_recomputes_scientific_metrics_and_rejects_forgery(
    saved_correctness_run, metric: str, forged_value: object
) -> None:
    from km_quantum.qaoa.benchmark import _validate_saved_run

    run, reference, manifest, environment, output = saved_correctness_run
    _validate_saved_run(run, reference, manifest, environment, output)
    run[metric] = forged_value
    with pytest.raises(ValueError, match="differs"):
        _validate_saved_run(run, reference, manifest, environment, output)


def test_resume_recomputes_sample_counts_instead_of_trusting_embedded_metrics(
    saved_correctness_run,
) -> None:
    from km_quantum.qaoa.benchmark import _validate_saved_run

    run, reference, manifest, environment, output = saved_correctness_run
    run["sampling"]["empirical_ground_frequency"] = 0.0
    sample_path = output / "samples" / f"{reference.name}-p1-seed0.json"
    sample_path.write_text(json.dumps(run["sampling"]), encoding="utf-8")
    # Updating the hash as well must not make invented scientific metrics valid.
    run["sample_sha256"] = hashlib.sha256(sample_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="recomputed sampling.empirical_ground_frequency"):
        _validate_saved_run(run, reference, manifest, environment, output)
