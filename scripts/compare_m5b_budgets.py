"""Compare exactly two advisory budgets using immutable local M5 evidence.

No account, service, primitive, submission, approval or hardware preparation is
created. Existing executable-plan guards deliberately remain unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from km_quantum.m4.io import lf_file_hash
from km_quantum.m4.metrics import build_plan_table
from km_quantum.m5.approvals import plan_hash, validate_plan
from km_quantum.m5.budget_comparison import compare_budget_variants, validate_budget_comparison
from km_quantum.m5.budget_precision import precision_from_distribution
from km_quantum.m5.budget_timing import timing_components
from km_quantum.m5.io import (
    dependency_versions,
    historical_integrity,
    raw_hash,
    read_json,
    read_sealed,
    sealed_json,
)
from km_quantum.m5.manifest import git_context, source_context
from km_quantum.m5.snapshots import load_qpy
from km_quantum.m5.usage import validate_usage_model
from km_quantum.m5.workflow import artifact_path, verify_plan_inventory_binding
from km_quantum.qaoa.references import load_references

BASELINE_HEAD = "5d771d227ddf5036007173f8eac1c45bb46c044f"
BASELINE_PLAN = "a2288adfb1bd56336ef7df8ff33de4ed9085689c71c9537d655fd4cb473c17ac"


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify_inputs(root: Path, source: Path) -> dict[str, Any]:
    """Verify the original evidence without relabeling it as new preparation."""
    require(source.is_relative_to(root / "results"), "Source must be local results evidence")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASELINE_HEAD, "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    plan = read_json(source / "execution_plan.json")
    require(
        plan_hash(plan) == plan.get("plan_hash") == BASELINE_PLAN,
        "Unexpected original BLOCKED execution plan",
    )
    validate_plan(plan, require_hardware=False)
    require(
        plan["status"] == "BLOCKED" and plan["provider_verified"] is False,
        "The comparison requires the original unapproved blocked plan",
    )
    prepared = read_sealed(source / "prepared_manifest.json")
    snapshot = read_sealed(source / "target_snapshot.json")
    context = read_sealed(source / "preparation_context.json")
    model = read_json(source / "local_usage_model.json")
    validate_usage_model(model, plan, require_ready=False)
    require(plan["local_usage_model"] == model, "Stored model differs from the plan")
    verify_plan_inventory_binding(plan, prepared)
    require(plan["target_snapshot_sha256"] == snapshot["content_sha256"], "Snapshot differs")
    original_context = context["source_context"]
    require(
        original_context["content_sha256"] == plan["source_context_sha256"],
        "Original source context differs",
    )
    require(original_context["dependencies"] == dependency_versions(), "Original pins changed")
    for relative, expected in original_context["source_files_lf_sha256"].items():
        path = (root / relative).resolve()
        require(path.is_relative_to(root), "Source path escaped the repository")
        require(lf_file_hash(path) == expected, "Original model/execution source changed")
    selection = read_sealed(root / "data/synthetic/m5/selection_manifest.json")
    require(
        selection["content_sha256"] == plan["selection_manifest_sha256"],
        "Original scientific selection changed",
    )
    require(
        selection["protocol_lf_sha256"] == lf_file_hash(root / "docs/m5_experiment_protocol.md"),
        "Original scientific protocol changed",
    )
    require(
        selection["work_order_sha256"]
        == raw_hash(root / "KM3_M5_IBM_Hardware_Validation_Prompt.md"),
        "Original M5 work order changed",
    )
    require(historical_integrity(root)["status"] == "PASS", "Protected references changed")
    require(snapshot["backend_name"] == plan["backend"] == "ibm_fez", "Backend differs")
    circuits, predictions, hashes = {}, {}, []
    require(len(prepared["records"]) == 28, "Original circuit inventory is incomplete")
    for record in prepared["records"]:
        identity = record["circuit_id"]
        require(
            read_sealed(source / "circuits" / identity / "record.json") == record,
            "Original circuit record changed",
        )
        for role, metadata in record["qpy"].items():
            path = artifact_path(root, metadata["path"], source)
            require(raw_hash(path) == metadata["sha256"], "Original QPY raw bytes changed")
            hashes.append(
                {
                    "circuit_id": identity,
                    "role": role,
                    "path": metadata["path"],
                    "raw_sha256": metadata["sha256"],
                }
            )
        metadata = record["qpy"]["provider"]
        circuits[identity] = load_qpy(
            artifact_path(root, metadata["path"], source), expected_sha256=metadata["sha256"]
        )
        prediction = read_sealed(source / "predictions" / (identity + ".json"))
        require(prediction["content_sha256"] == record["prediction_sha256"], "Prediction changed")
        require(
            prediction["target_snapshot_sha256"] == snapshot["content_sha256"],
            "Prediction refers to another snapshot",
        )
        require(
            prediction["hardware_used"] is False and prediction["frozen_before_results"] is True,
            "Prediction provenance differs",
        )
        require(prediction["device_noise"]["status"] == "READY", "Original noise model blocked")
        predictions[identity] = prediction
    require(len(hashes) == 84, "Original QPY inventory is incomplete")
    return {
        "plan": plan,
        "model": model,
        "prepared": prepared,
        "snapshot": snapshot,
        "preparation_context": context,
        "circuits": circuits,
        "predictions": predictions,
        "raw_qpy_inventory": hashes,
    }


def timing_diagnostics(inputs: dict[str, Any]) -> dict[str, Any]:
    rows, summaries = [], []
    for job in inputs["model"]["jobs"]:
        native, readout, initialization, repetition = [], [], [], []
        for pub in job["pubs"]:
            require(
                pub["expanded_circuit_count"] == 1 and len(pub["timings"]) == 1,
                "The frozen M5 circuits must remain scalar fully-bound PUBs",
            )
            archived = pub["timings"][0]
            component = timing_components(
                inputs["circuits"][pub["circuit_id"]],
                inputs["snapshot"],
                archived_circuit_seconds=archived["circuit_seconds"],
            )
            require(component["status"] == "COMPUTED", "Snapshot timing diagnosis is incomplete")
            require(
                component["archived_duration_comparison"]["status"] == "MATCH",
                "Independent critical path differs from the archived model",
            )
            require(
                math.isclose(
                    component["initialization_seconds"],
                    archived["initialization_seconds"],
                    rel_tol=0,
                    abs_tol=1e-15,
                ),
                "Initialization timing differs",
            )
            shots = archived["shots"]
            native.append(shots * component["native_without_readout_seconds"])
            readout.append(shots * component["readout_critical_increment_seconds"])
            initialization.append(shots * component["initialization_seconds"])
            repetition.append(shots * job["rep_delay"]["seconds"])
            rows.append(
                {
                    "job_id": job["job_id"],
                    "pub_id": pub["pub_id"],
                    "circuit_id": pub["circuit_id"],
                    "qpy_sha256": pub["qpy_sha256"],
                    "shots": shots,
                    "diagnostic_timing": component,
                }
            )
        total = math.fsum(native + readout + initialization + repetition)
        require(
            math.isclose(total, job["shot_time_seconds"], rel_tol=0, abs_tol=1e-12),
            "Independent shot-time decomposition differs from the archived model",
        )
        summaries.append(
            {
                "job_id": job["job_id"],
                "native_without_readout_seconds": math.fsum(native),
                "readout_critical_increment_seconds": math.fsum(readout),
                "initialization_seconds": math.fsum(initialization),
                "rep_delay_seconds": math.fsum(repetition),
                "shot_time_seconds": total,
                "original_config_sha256": job["config_sha256"],
                "original_target_durations_sha256": job["target_durations_sha256"],
            }
        )
    return {
        "artifact_type": "m5b_advisory_timing_diagnostics",
        "status": "COMPUTED",
        "units": "seconds",
        "full_live_target_claimed": False,
        "source_plan_hash": inputs["plan"]["plan_hash"],
        "target_snapshot_sha256": inputs["snapshot"]["content_sha256"],
        "snapshot_retrieved_at": inputs["snapshot"]["retrieved_at"],
        "calibration_timestamp": inputs["snapshot"]["calibration_timestamp"],
        "job_summaries": summaries,
        "pub_diagnostics": rows,
        "provider_contacted": False,
        "hardware_used": False,
        "measurement_definition": (
            "Critical-path increment of readout; never a sum of parallel wires"
        ),
    }


def compute(root: Path, source: Path, output: Path) -> dict[str, Any]:
    require(output.is_relative_to(root / "results"), "Output must be a new local results directory")
    require(not output.exists(), "Keep historical outputs; choose a new output version")
    inputs = verify_inputs(root, source)
    context = source_context(root)
    comparison = compare_budget_variants(inputs["plan"], inputs["model"], source_head=BASELINE_HEAD)
    validate_budget_comparison(comparison, inputs["plan"], inputs["model"])
    diagnostics = timing_diagnostics(inputs)
    references = load_references(root)
    tables = {
        name: build_plan_table(references[name])
        for name in ("A_single_job", "E_overlap", "H_workload", "K_bounded_slack")
    }
    precision = []
    for record in inputs["prepared"]["records"]:
        if record["kind"] != "main":
            continue
        identity, name = record["circuit_id"], record["reference_name"]
        prediction = inputs["predictions"][identity]
        binding = {
            "source_plan_hash": inputs["plan"]["plan_hash"],
            "prediction_content_sha256": prediction["content_sha256"],
            "provider_qpy_sha256": record["qpy"]["provider"]["sha256"],
            "target_snapshot_sha256": inputs["snapshot"]["content_sha256"],
        }
        for label, vector in (
            ("compiled_ideal", prediction["compiled_ideal"]),
            (
                "device_noise_after_readout",
                prediction["device_noise"]["probabilities_after_readout"],
            ),
        ):
            precision.append(
                precision_from_distribution(
                    references[name],
                    vector,
                    circuit_id=identity,
                    prediction_label=label,
                    prediction_reference=binding,
                    table=tables[name],
                )
            )
    require(len(precision) == 48, "The four cases and all 24 main circuits must remain represented")
    require(source_context(root) == context, "Comparison code changed during the calculation")
    output.mkdir(parents=True)
    origin = {
        "baseline_head": BASELINE_HEAD,
        "source_plan_hash": inputs["plan"]["plan_hash"],
        "original_usage_model_sha256": inputs["model"]["model_sha256"],
        "target_snapshot_sha256": inputs["snapshot"]["content_sha256"],
        "original_source_context_sha256": inputs["plan"]["source_context_sha256"],
        "comparison_source_context_sha256": context["content_sha256"],
        "comparison_script_raw_sha256": raw_hash(Path(__file__)),
        "work_order_raw_sha256": raw_hash(root / "KM3_M5B_Budget_Plan_Comparison_Prompt.md"),
        "git": git_context(root),
        "source_directory": source.relative_to(root).as_posix(),
        "snapshot_retrieved_at": inputs["snapshot"]["retrieved_at"],
        "calibration_timestamp": inputs["snapshot"]["calibration_timestamp"],
    }
    sealed_json(
        output / "comparison.json",
        {
            "artifact_type": "m5b_local_comparison_run",
            "status": "ADVISORY_ONLY",
            "origin": origin,
            "comparison": comparison,
            "provider_contacted": False,
            "submission_eligible": False,
            "hardware_enabled": False,
            "submission_approved": False,
            "hardware_used": False,
        },
    )
    for variant, filename in zip(
        comparison["variants"],
        ("variant_a_shot_proposal.json", "variant_b_budget_amendment.json"),
        strict=True,
    ):
        sealed_json(
            output / filename,
            {
                **variant,
                "origin": origin,
                "parent_comparison_draft_sha256": comparison["draft_sha256"],
                "submission_eligible": False,
                "provider_contacted": False,
                "hardware_used": False,
            },
        )
    sealed_json(output / "timing_breakdown.json", diagnostics)
    sealed_json(
        output / "precision_comparison.json",
        {
            "artifact_type": "m5b_offline_precision_comparison",
            "source": "offline_reference",
            "origin": origin,
            "circuit_predictions": precision,
            "hardware_used": False,
            "empirical_hardware_counts_created": False,
            "blocks_pooled": False,
        },
    )
    sealed_json(
        output / "input_verification.json",
        {
            "artifact_type": "m5b_comparison_input_verification",
            "status": "PASS",
            "origin": origin,
            "raw_qpy_inventory": inputs["raw_qpy_inventory"],
            "original_code_files_unchanged": True,
            "provider_contacted": False,
            "seals_verified": True,
            "historical_integrity": historical_integrity(root),
        },
    )
    return {
        "status": "ADVISORY_ONLY",
        "source_plan_hash": inputs["plan"]["plan_hash"],
        "comparison_draft_sha256": comparison["draft_sha256"],
        "verified_qpy_files": len(inputs["raw_qpy_inventory"]),
        "variants": 2,
        "provider_contacted": False,
        "qpu_jobs_submitted": 0,
        "output": output.as_posix(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source", type=Path, default=Path("results/m5b-fix-20261012-v2/provider"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    source = args.source.resolve() if args.source.is_absolute() else (root / args.source).resolve()
    output = args.output.resolve() if args.output.is_absolute() else (root / args.output).resolve()
    print(json.dumps(compute(root, source, output), ensure_ascii=False))


if __name__ == "__main__":
    main()
