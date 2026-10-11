"""Prespecified identities, input hashes and bounded run plan for M4."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from km_quantum.m4.io import (
    canonical_hash,
    lf_file_hash,
    read_json,
    verify_reference_integrity,
    write_json,
)
from km_quantum.m4.models import M4Config
from km_quantum.m4.noise import (
    DEFAULT_NOISE_PROFILES,
    CompilerConfig,
    ResourceLimits,
    resource_preflight,
)
from km_quantum.m4.suite import load_m4_references

MANIFEST_PATH = Path("data/synthetic/m4/benchmark_manifest.json")
CONFIG_PATH = Path("configs/m4_baseline.json")
ALGORITHMS = ("standard", "warm_start")
CONTROLS = ("uniform", "rounded_relaxation", "warm_product")
INPUT_PATHS = (
    "data/synthetic/m4/provenance.json",
    "data/synthetic/m4/references.jsonl",
    "data/synthetic/m4/generator_configs.json",
    "data/synthetic/m4/selection_audit.json",
    "results/m4/metric_semantics_audit.json",
    "results/m4/preflight/m3_reverification.json",
    "results/m4/preflight/resource_smoke.json",
    "docs/m4_experiment_protocol.md",
    "docs/m4_warm_start_formulation.md",
)


def resource_limits(config: M4Config) -> ResourceLimits:
    return ResourceLimits(
        **{
            key: getattr(config, key)
            for key in (
                "max_qubits",
                "max_memory_mb",
                "max_parallel_threads",
                "max_instances",
                "max_training_runs",
                "max_density_runs",
            )
        }
    )


def load_config(root: Path, path: Path | None = None) -> M4Config:
    return M4Config.model_validate(read_json(path or root / CONFIG_PATH))


def source_content_hash(root: Path) -> str:
    """Permit reporting-only commits while refusing any changed experiment code."""
    paths = sorted(root.glob("src/**/*.py"))
    paths += sorted(root.glob("tests/**/*.py"))
    paths += [root / "pyproject.toml", root / "uv.lock"]
    return canonical_hash({path.relative_to(root).as_posix(): lf_file_hash(path) for path in paths})


def freeze_manifest(root: Path, config: M4Config | None = None) -> dict[str, Any]:
    config = config or load_config(root)
    if not verify_reference_integrity(root)["unchanged"]:
        raise ValueError("Historical M1/M2/M3 references changed")
    existing_path = root / MANIFEST_PATH
    if existing_path.exists():
        return validate_manifest(root, config)
    references = load_m4_references(root)
    audit = read_json(root / "data/synthetic/m4/selection_audit.json")
    selections = [row for row in audit["selections"] if row["status"] == "SELECTED"]
    training, noise, controls, entries = [], [], [], []
    for instance_index, selection in enumerate(selections):
        name = selection["reference_id"]
        reference = references[name]
        group = "known_diagnostic" if selection["already_observed_in_m3"] else "new_evaluation"
        entries.append(
            {
                "reference_id": name,
                "instance_id": reference.instance.instance_id,
                "reference_hash": reference.reference_hash,
                "qubo_hash": reference.qubo_hash,
                "ising_hash": reference.ising_hash,
                "qubit_count": reference.qubo.number_variables,
                "ground_encoding_count": len(reference.ground_bits),
                "ground_energy": reference.ground_energy,
                "group": group,
                "split": selection["split"],
                "selection_reason": selection["selection_reason"],
                "exploratory_known_weakness": selection.get("exploratory_known_weakness", False),
                "structural_metadata": reference.qubo.metrics().model_dump(mode="json"),
                "cp_sat_reference": selection["cp_sat_reference"],
            }
        )
        for control_index, control in enumerate(CONTROLS):
            controls.append(
                {
                    "control_id": f"{name}-{control}",
                    "reference_id": name,
                    "control": control,
                    "sampling_seed": config.sample_seed_base
                    + 3000000
                    + instance_index * 100
                    + control_index,
                }
            )
        for algorithm_index, algorithm in enumerate(ALGORITHMS):
            for p in config.depths:
                for restart_index, seed in enumerate(config.restart_seeds):
                    training_id = f"{name}-{algorithm}-p{p}-seed{seed}"
                    common = {
                        "training_id": training_id,
                        "reference_id": name,
                        "algorithm": algorithm,
                        "p": p,
                        "initialization_seed": seed,
                    }
                    training.append(common)
                    for profile_index, profile in enumerate(DEFAULT_NOISE_PROFILES):
                        noise.append(
                            {
                                **common,
                                "run_id": f"{training_id}-{profile.name}",
                                "noise_profile": asdict(profile),
                                "sampling_seed": config.sample_seed_base
                                + instance_index * 10000
                                + algorithm_index * 1000
                                + p * 100
                                + restart_index * 10
                                + profile_index,
                            }
                        )
    counts = {
        "instances": len(entries),
        "training": len(training),
        "density": len(noise),
        "controls": len(controls),
        "preparations": len(entries),
        "cp_sat_references": len(entries),
    }
    resources = resource_preflight(
        max(row["qubit_count"] for row in entries),
        resource_limits(config),
        instance_count=len(entries),
        training_runs=len(training),
        density_runs=len(noise),
    )
    provenance = read_json(root / "data/synthetic/m4/provenance.json")
    result: dict[str, Any] = {
        "schema_version": 4,
        "milestone": "M4",
        "m3_reference_commit": provenance["m3_reference_commit"],
        "reported_m3_completion_commit": provenance["reported_m3_completion_commit"],
        "m3_training_commit": provenance["m3_training_commit"],
        "m3_manifest_content_sha256": provenance["m3_manifest_content_sha256"],
        "config_hash": canonical_hash(config.model_dump(mode="json")),
        "configuration": config.model_dump(mode="json"),
        "inputs": {path: lf_file_hash(root / path) for path in INPUT_PATHS},
        "references": entries,
        "counts": counts,
        "noise_profiles": [asdict(p) for p in DEFAULT_NOISE_PROFILES],
        "compiler": asdict(CompilerConfig(seed=config.transpiler_seed)),
        "initial_resource_preflight": resources,
        "planned_training_runs": training,
        "planned_noise_runs": noise,
        "planned_control_runs": controls,
        "sampler_seed_rule": (
            "base + instance_index*10000 + algorithm_index*1000 + p*100 + "
            "restart_index*10 + profile_index; controls use "
            "base+3000000+instance_index*100+control_index"
        ),
        "primary_metric": "p_schedule_optimal",
        "secondary_metric": "p_ground_encoding",
        "aggregation": (
            "Restart distributions within each instance; equal weighting of instance medians; "
            "known and new groups separate; paired restart differences also retained."
        ),
        "training_noise_used": False,
        "hardware_used": False,
        "selection_uses_qaoa_outcomes": False,
        "p3_analysis_planned": False,
    }
    result["manifest_content_sha256"] = canonical_hash(result)
    write_json(existing_path, result, immutable=True)
    return read_json(existing_path)


def expected_plan_rows(
    entries: list[dict[str, Any]], config: M4Config
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    training, noise, controls = [], [], []
    for instance_index, entry in enumerate(entries):
        name = entry["reference_id"]
        for control_index, control in enumerate(CONTROLS):
            controls.append(
                {
                    "control_id": f"{name}-{control}",
                    "reference_id": name,
                    "control": control,
                    "sampling_seed": config.sample_seed_base
                    + 3000000
                    + instance_index * 100
                    + control_index,
                }
            )
        for algorithm_index, algorithm in enumerate(ALGORITHMS):
            for p in config.depths:
                for restart_index, seed in enumerate(config.restart_seeds):
                    training_id = f"{name}-{algorithm}-p{p}-seed{seed}"
                    common = {
                        "training_id": training_id,
                        "reference_id": name,
                        "algorithm": algorithm,
                        "p": p,
                        "initialization_seed": seed,
                    }
                    training.append(common)
                    for profile_index, profile in enumerate(DEFAULT_NOISE_PROFILES):
                        noise.append(
                            {
                                **common,
                                "run_id": f"{training_id}-{profile.name}",
                                "noise_profile": asdict(profile),
                                "sampling_seed": config.sample_seed_base
                                + instance_index * 10000
                                + algorithm_index * 1000
                                + p * 100
                                + restart_index * 10
                                + profile_index,
                            }
                        )
    return training, noise, controls


def validate_manifest(root: Path, config: M4Config | None = None) -> dict[str, Any]:
    config = config or load_config(root)
    result = read_json(root / MANIFEST_PATH)
    content = {key: value for key, value in result.items() if key != "manifest_content_sha256"}
    if result["manifest_content_sha256"] != canonical_hash(content):
        raise ValueError("Frozen M4 manifest hash mismatch")
    if result["configuration"] != config.model_dump(mode="json") or result[
        "config_hash"
    ] != canonical_hash(config.model_dump(mode="json")):
        raise ValueError("M4 configuration differs from the frozen manifest")
    if result["noise_profiles"] != [asdict(p) for p in DEFAULT_NOISE_PROFILES]:
        raise ValueError("M4 noise profiles differ from the frozen manifest")
    if canonical_hash(result["compiler"]) != canonical_hash(
        asdict(CompilerConfig(seed=config.transpiler_seed))
    ):
        raise ValueError("M4 compiler policy differs from the frozen manifest")
    for path, expected in result["inputs"].items():
        if lf_file_hash(root / path) != expected:
            raise ValueError(f"Frozen M4 input changed: {path}")
    if not verify_reference_integrity(root)["unchanged"]:
        raise ValueError("Historical reference integrity failed")
    references = load_m4_references(root)
    if set(references) != {row["reference_id"] for row in result["references"]}:
        raise ValueError("M4 reference selection differs")
    for row in result["references"]:
        ref = references[row["reference_id"]]
        if (row["reference_hash"], row["qubo_hash"], row["ising_hash"], row["qubit_count"]) != (
            ref.reference_hash,
            ref.qubo_hash,
            ref.ising_hash,
            ref.qubo.number_variables,
        ):
            raise ValueError("M4 reference identities differ")
    selections = {
        row["reference_id"]: row
        for row in read_json(root / "data/synthetic/m4/selection_audit.json")["selections"]
        if row["status"] == "SELECTED"
    }
    for row in result["references"]:
        selected = selections[row["reference_id"]]
        expected_group = (
            "known_diagnostic" if selected["already_observed_in_m3"] else "new_evaluation"
        )
        if row["group"] != expected_group or row["split"] != selected["split"]:
            raise ValueError("Known diagnostic/new evaluation labels differ from frozen selection")
        if canonical_hash(row["cp_sat_reference"]) != canonical_hash(selected["cp_sat_reference"]):
            raise ValueError("Classical reference differs from the frozen selection audit")
    expected_rows = expected_plan_rows(result["references"], config)
    for key, expected in zip(
        ("planned_training_runs", "planned_noise_runs", "planned_control_runs"),
        expected_rows,
        strict=True,
    ):
        if canonical_hash(result[key]) != canonical_hash(expected):
            raise ValueError(f"M4 configured plan differs: {key}")
    if result["counts"] != {
        "instances": len(result["references"]),
        "training": len(result["planned_training_runs"]),
        "density": len(result["planned_noise_runs"]),
        "controls": len(result["planned_control_runs"]),
        "preparations": len(result["references"]),
        "cp_sat_references": len(result["references"]),
    }:
        raise ValueError("M4 planned counts differ")
    resource_preflight(
        max(row["qubit_count"] for row in result["references"]),
        resource_limits(config),
        instance_count=result["counts"]["instances"],
        training_runs=result["counts"]["training"],
        density_runs=result["counts"]["density"],
    )
    return result
