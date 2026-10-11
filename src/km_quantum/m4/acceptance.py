"""Separate implementation, complete experiment and research outcomes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from km_quantum.m4.audit import audit_experiment
from km_quantum.m4.benchmark import load_records
from km_quantum.m4.io import read_json, verify_reference_integrity, write_json
from km_quantum.m4.manifest import validate_manifest
from km_quantum.m4.models import M4Config
from km_quantum.m4.reporting import summarize_records

PLOT_NAMES = (
    "standard_vs_warm_start.png",
    "warm_product_vs_qaoa.png",
    "noise_influence.png",
    "encoding_vs_schedule_feasibility.png",
    "case_k_exploratory.png",
)


def build_acceptance(
    root: Path,
    output: Path,
    manifest: dict[str, Any],
    config: M4Config,
    tooling: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_manifest(root, config)
    audit = audit_experiment(root, output, manifest, config)
    records, controls = load_records(output)
    summary = summarize_records(records, controls, manifest)
    gate = (
        read_json(output / "verification.json") if (output / "verification.json").exists() else {}
    )
    integrity = verify_reference_integrity(root)
    evidence = tooling or {}
    tooling_passed = bool(evidence) and all(
        isinstance(evidence.get(name), dict) and evidence[name].get("status") == "PASS"
        for name in ("pytest", "ruff", "format", "mypy", "build")
    )
    experiment_complete = (
        audit["status"] == "PASS"
        and audit.get("scope") == "primary"
        and summary["status"] == "COMPLETE"
    )
    gates_passed = (
        gate.get("status") == "PASS"
        and integrity["unchanged"]
        and gate.get("config_hash") == manifest["config_hash"]
        and gate.get("manifest_content_sha256") == manifest["manifest_content_sha256"]
        and all(
            gate.get(key) is True
            for key in (
                "metric_semantics_verified",
                "warm_start_mixer_verified",
                "zero_noise_equivalence_verified",
                "readout_model_verified",
                "operator_equivalence",
            )
        )
    )
    report_exists = (root / "docs/m4_completion_report.md").is_file()
    plot_paths = sorted((output / "plots").glob("*.png"))
    plots_complete = {path.name for path in plot_paths} == set(PLOT_NAMES) and all(
        path.stat().st_size > 0 for path in plot_paths
    )
    status = (
        "PASS"
        if experiment_complete
        and gates_passed
        and tooling_passed
        and report_exists
        and plots_complete
        else "INCOMPLETE"
    )
    if gate.get("status") == "FAIL" or audit["status"] == "INVALID" or not integrity["unchanged"]:
        status = "FAIL"
    implementation = "PASS" if gates_passed and tooling_passed else "INCOMPLETE"
    if gate.get("status") == "FAIL" or not integrity["unchanged"]:
        implementation = "FAIL"
    result = {
        "milestone": "M4",
        "status": status,
        "implementation_status": implementation,
        "experiment_status": {
            "status": "COMPLETE" if experiment_complete else "INCOMPLETE",
            "scope": audit.get("scope"),
            "planned": manifest["counts"],
            "verified_executed": audit["checked"],
            "failed": audit["failed"],
            "missing": audit["missing"],
            "unexpected": audit["unexpected"],
            "summary_counts": summary["experiment_counts"],
        },
        "reference_integrity": integrity,
        "metric_semantics_verified": gate.get("metric_semantics_verified", False),
        "warm_start_mixer_verified": gate.get("warm_start_mixer_verified", False),
        "zero_noise_equivalence_verified": gate.get("zero_noise_equivalence_verified", False),
        "readout_model_verified": gate.get("readout_model_verified", False),
        "operator_equivalence": gate.get("operator_equivalence", False),
        "manifest_content_sha256": manifest["manifest_content_sha256"],
        "config_hash": manifest["config_hash"],
        "m3_reference_commit": manifest["m3_reference_commit"],
        "artifact_audit": audit,
        "hypothesis_results": {
            "interpretation": (
                "Descriptive equal-instance comparisons; outcome quality is not acceptance."
            ),
            "algorithm_comparisons": summary["equal_instance_weighted_comparisons"],
            "classical_initialization_increments": summary[
                "increments_over_classical_initial_distribution"
            ],
            "fixed_parameter_noise_changes": summary["fixed_parameter_noise_changes"],
            "case_k": summary["case_k_exploratory"],
            "significance_or_quantum_advantage_claimed": False,
        },
        "tooling_passed": tooling_passed,
        "tooling": tooling,
        "completion_report_exists": report_exists,
        "required_plots_complete": plots_complete,
        "hardware_used": False,
        "credentials_used": False,
        "training_noise_used": False,
        "performance_is_acceptance_criterion": False,
    }
    write_json(output / "m4-acceptance.json", result)
    return result
