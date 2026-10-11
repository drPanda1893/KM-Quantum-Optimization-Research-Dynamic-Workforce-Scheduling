"""Descriptive reporting: restarts within instances, then equal instance weights."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

PROBABILITY_METRICS = (
    "p_schedule_optimal",
    "p_ground_encoding",
    "p_encoding_feasible",
    "p_schedule_feasible",
)
QUALITY_METRICS = (*PROBABILITY_METRICS, "expected_qubo_energy", "normalized_energy_error")
DATASETS = ("known_diagnostic", "new_evaluation")


def _stats(values: list[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(array)):
        raise ValueError("Reporting requires finite actual numeric observations")
    if not len(array):
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "sample_std": None,
            "minimum": None,
            "maximum": None,
            "q25": None,
            "q75": None,
        }
    return {
        "count": len(array),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "sample_std": float(np.std(array, ddof=1)) if len(array) > 1 else 0.0,
        "minimum": float(np.min(array)),
        "maximum": float(np.max(array)),
        "q25": float(np.quantile(array, 0.25)),
        "q75": float(np.quantile(array, 0.75)),
    }


def _profile(record: dict[str, Any]) -> str:
    return str(record["noise_profile"]["name"])


def _checked_quality(values: dict[str, Any]) -> dict[str, float]:
    result = {name: float(values[name]) for name in QUALITY_METRICS}
    if not all(np.isfinite(value) for value in result.values()):
        raise ValueError("nonfinite metric")
    if any(not -1e-12 <= result[name] <= 1 + 1e-12 for name in PROBABILITY_METRICS):
        raise ValueError("probability outside [0,1]")
    if not (
        result["p_ground_encoding"] <= result["p_schedule_optimal"] + 1e-12
        and result["p_schedule_optimal"] <= result["p_schedule_feasible"] + 1e-12
        and result["p_encoding_feasible"] <= result["p_schedule_feasible"] + 1e-12
    ):
        raise ValueError("metric containment failed")
    return result


def _audit_records(
    records: list[dict[str, Any]],
    planned: list[dict[str, Any]],
    references: dict[str, dict[str, Any]],
    *,
    controls: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    id_field = "control_id" if controls else "run_id"
    expected = {row[id_field]: row for row in planned}
    if len(expected) != len(planned):
        raise ValueError("Manifest has duplicate planned identities")
    seen: dict[str, dict[str, Any]] = {}
    usable: list[dict[str, Any]] = []
    unexpected: list[Any] = []
    duplicates: list[str] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for record in records:
        identity = record.get(id_field)
        if controls and identity is None:
            identity = f"{record.get('reference_id')}-{record.get('control')}"
        if not isinstance(identity, str) or identity not in expected:
            unexpected.append(identity)
            continue
        if identity in seen:
            duplicates.append(identity)
            continue
        seen[identity] = record
        status = record.get("status", "MISSING_STATUS")
        if status != "COMPLETE":
            item = {id_field: identity, "status": status, "reason": record.get("reason")}
            (skipped if status in ("SKIPPED", "ABORTED") else failed).append(item)
            continue
        try:
            plan = expected[identity]
            reference = references[plan["reference_id"]]
            for key in (
                ("reference_id", "control")
                if controls
                else ("reference_id", "algorithm", "p", "initialization_seed")
            ):
                if record[key] != plan[key]:
                    raise ValueError(f"declared {key} differs from planned identity")
            if record["group"] != reference["group"]:
                raise ValueError("declared dataset differs from reference")
            if controls:
                _checked_quality(record["metrics"])
            else:
                if record["noise_profile"] != plan["noise_profile"]:
                    raise ValueError("noise profile differs from planned profile")
                for layer in ("ideal", "before_readout", "after_readout"):
                    _checked_quality(record["metrics"][layer])
        except (KeyError, ValueError, TypeError) as error:
            invalid.append({id_field: identity, "reason": str(error)})
            continue
        usable.append(record)
    missing = sorted(set(expected) - set(seen))
    complete = not (missing or unexpected or duplicates or failed or skipped or invalid)
    return usable, {
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "planned": len(expected),
        "record_count": len(records),
        "executed": len(seen),
        "completed": len(usable),
        "failed": failed,
        "skipped_or_aborted": skipped,
        "missing": missing,
        "unexpected": unexpected,
        "duplicates": duplicates,
        "invalid": invalid,
    }


def summarize_records(
    records: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Summarize persisted observations without treating shots/restarts as problems."""
    references = {row["reference_id"]: row for row in manifest["references"]}
    good, run_audit = _audit_records(
        records, manifest["planned_noise_runs"], references, controls=False
    )
    good_controls, control_audit = _audit_records(
        controls, manifest["planned_control_runs"], references, controls=True
    )
    control_index = {(row["reference_id"], row["control"]): row for row in good_controls}
    grouped: dict[tuple[str, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in good:
        grouped[
            (record["reference_id"], record["p"], record["algorithm"], _profile(record))
        ].append(record)
    planned_counts = Counter(
        (row["reference_id"], row["p"], row["algorithm"], row["noise_profile"]["name"])
        for row in manifest["planned_noise_runs"]
    )
    instance_rows = []
    for key, observations in sorted(grouped.items()):
        name, p, algorithm, profile = key
        observations.sort(key=lambda row: row["initialization_seed"])
        layers = {
            layer: {
                metric: _stats([float(row["metrics"][layer][metric]) for row in observations])
                for metric in QUALITY_METRICS
            }
            for layer in ("ideal", "before_readout", "after_readout")
        }
        uniform = control_index.get((name, "uniform"))
        uniform_ground = None if uniform is None else uniform["metrics"]["p_ground_encoding"]
        lift = (
            None
            if uniform_ground is None or uniform_ground == 0
            else _stats(
                [
                    row["metrics"]["after_readout"]["p_ground_encoding"] / uniform_ground
                    for row in observations
                ]
            )
        )
        timing_names = sorted(
            {
                key
                for observation in observations
                for key, value in observation.get("timings", {}).items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        )
        timings = {
            timing: _stats(
                [
                    float(row["timings"][timing])
                    for row in observations
                    if timing in row.get("timings", {})
                ]
            )
            for timing in timing_names
        }
        conditional = [
            float(value)
            for row in observations
            if (value := row["metrics"]["after_readout"].get("conditional_valid_m1_objective"))
            is not None
        ]
        instance_rows.append(
            {
                "reference_id": name,
                "group": references[name]["group"],
                "qubit_count": references[name].get("qubit_count"),
                "p": p,
                "algorithm": algorithm,
                "noise_profile": profile,
                "restart_count": len(observations),
                "planned_restart_count": planned_counts[key],
                "complete_restart_group": len(observations) == planned_counts[key],
                "initialization_seeds": [row["initialization_seed"] for row in observations],
                "metrics": layers,
                "uniform_ground_probability": uniform_ground,
                "ground_lift_after_readout": lift,
                "conditional_valid_m1_objective": _stats(conditional),
                "conditional_quality_requires_schedule_feasibility": True,
                "timings_ms": timings,
                "raw_run_ids": [row["run_id"] for row in observations],
            }
        )
    by_group = {
        (row["reference_id"], row["p"], row["algorithm"], row["noise_profile"]): row
        for row in instance_rows
    }
    by_restart = {
        (
            row["reference_id"],
            row["p"],
            row["algorithm"],
            _profile(row),
            row["initialization_seed"],
        ): row
        for row in good
    }
    pairs = []
    combinations = sorted({(key[0], key[1], key[3]) for key in grouped})
    for name, p, profile in combinations:
        standard = by_group.get((name, p, "standard", profile))
        warm = by_group.get((name, p, "warm_start", profile))
        if standard is None or warm is None:
            continue
        seeds = sorted(set(standard["initialization_seeds"]) & set(warm["initialization_seeds"]))
        differences: dict[str, list[float]] = {}
        for metric in QUALITY_METRICS:
            differences[metric] = [
                by_restart[(name, p, "warm_start", profile, seed)]["metrics"]["after_readout"][
                    metric
                ]
                - by_restart[(name, p, "standard", profile, seed)]["metrics"]["after_readout"][
                    metric
                ]
                for seed in seeds
            ]
        pairs.append(
            {
                "reference_id": name,
                "group": references[name]["group"],
                "p": p,
                "noise_profile": profile,
                "paired_seeds": seeds,
                "complete_restart_pairs": (
                    standard["complete_restart_group"]
                    and warm["complete_restart_group"]
                    and len(seeds) == standard["planned_restart_count"]
                ),
                "paired_restart_differences_warm_minus_standard": {
                    metric: _stats(values) for metric, values in differences.items()
                },
                "paired_restart_differences_percentage_points": {
                    metric: _stats([100 * value for value in differences[metric]])
                    for metric in PROBABILITY_METRICS
                },
                "difference_of_restart_medians_warm_minus_standard": {
                    metric: warm["metrics"]["after_readout"][metric]["median"]
                    - standard["metrics"]["after_readout"][metric]["median"]
                    for metric in QUALITY_METRICS
                },
                "paired_restart_values": [
                    {"seed": seed, **{metric: values[i] for metric, values in differences.items()}}
                    for i, seed in enumerate(seeds)
                ],
            }
        )
    increments = []
    for row in instance_rows:
        name, algorithm = row["reference_id"], row["algorithm"]
        initial_name = "warm_product" if algorithm == "warm_start" else "uniform"
        initial = control_index.get((name, initial_name))
        if initial is None:
            continue
        increments.append(
            {
                "reference_id": name,
                "group": row["group"],
                "algorithm": algorithm,
                "p": row["p"],
                "noise_profile": row["noise_profile"],
                "initial_control": initial_name,
                "initial_control_layer": "ideal classical distribution without QAOA",
                "final_layer": "after_readout",
                "complete_restart_group": row["complete_restart_group"],
                "baseline_probabilities": {
                    metric: initial["metrics"][metric] for metric in PROBABILITY_METRICS
                },
                "difference_of_restart_median_from_initial": {
                    metric: row["metrics"]["after_readout"][metric]["median"]
                    - initial["metrics"][metric]
                    for metric in PROBABILITY_METRICS
                },
                "difference_percentage_points": {
                    metric: 100
                    * (
                        row["metrics"]["after_readout"][metric]["median"]
                        - initial["metrics"][metric]
                    )
                    for metric in PROBABILITY_METRICS
                },
            }
        )
    cohorts = []
    cohort_keys = sorted({(row["group"], row["p"], row["noise_profile"]) for row in instance_rows})
    for group, p, profile in cohort_keys:
        algorithm_rows = {}
        for algorithm in ("standard", "warm_start"):
            eligible = [
                row
                for row in instance_rows
                if (row["group"], row["p"], row["noise_profile"], row["algorithm"])
                == (group, p, profile, algorithm)
                and row["complete_restart_group"]
            ]
            algorithm_rows[algorithm] = {
                "instance_count": len(eligible),
                "reference_ids": [row["reference_id"] for row in eligible],
                "instance_median_statistics": {
                    metric: _stats(
                        [row["metrics"]["after_readout"][metric]["median"] for row in eligible]
                    )
                    for metric in QUALITY_METRICS
                },
            }
        eligible_pairs = [
            row
            for row in pairs
            if (row["group"], row["p"], row["noise_profile"]) == (group, p, profile)
            and row["complete_restart_pairs"]
        ]
        cohorts.append(
            {
                "group": group,
                "p": p,
                "noise_profile": profile,
                "aggregation_unit": "one restart median per instance; equal instance weights",
                "algorithms": algorithm_rows,
                "paired_instance_count": len(eligible_pairs),
                "instance_difference_of_medians": {
                    metric: _stats(
                        [
                            row["difference_of_restart_medians_warm_minus_standard"][metric]
                            for row in eligible_pairs
                        ]
                    )
                    for metric in QUALITY_METRICS
                },
                "instance_median_of_paired_restart_differences": {
                    metric: _stats(
                        [
                            row["paired_restart_differences_warm_minus_standard"][metric]["median"]
                            for row in eligible_pairs
                        ]
                    )
                    for metric in QUALITY_METRICS
                },
                "increment_over_initial_control": {
                    algorithm: {
                        metric: _stats(
                            [
                                row["difference_of_restart_median_from_initial"][metric]
                                for row in increments
                                if (row["group"], row["p"], row["noise_profile"], row["algorithm"])
                                == (group, p, profile, algorithm)
                                and row["complete_restart_group"]
                            ]
                        )
                        for metric in PROBABILITY_METRICS
                    }
                    for algorithm in ("standard", "warm_start")
                },
            }
        )
    noise_changes = []
    for key, observations in sorted(grouped.items()):
        name, p, algorithm, profile = key
        if profile == "ideal":
            continue
        ideal = by_group.get((name, p, algorithm, "ideal"))
        if ideal is None:
            continue
        paired = [
            (row, by_restart[(name, p, algorithm, "ideal", row["initialization_seed"])])
            for row in observations
            if (name, p, algorithm, "ideal", row["initialization_seed"]) in by_restart
        ]
        layer_changes = {
            layer: {
                metric: _stats(
                    [
                        row["metrics"][layer][metric] - baseline["metrics"]["ideal"][metric]
                        for row, baseline in paired
                    ]
                )
                for metric in QUALITY_METRICS
            }
            for layer in ("before_readout", "after_readout")
        }
        sequence_matches = [
            row.get("compiler", {}).get("sequence_sha256")
            == baseline.get("compiler", {}).get("sequence_sha256")
            for row, baseline in paired
            if "sequence_sha256" in row.get("compiler", {})
            and "sequence_sha256" in baseline.get("compiler", {})
        ]
        noise_changes.append(
            {
                "reference_id": name,
                "group": references[name]["group"],
                "algorithm": algorithm,
                "p": p,
                "noise_profile": profile,
                "paired_restart_count": len(paired),
                "fixed_training_ids_verified": all(
                    row["training_id"] == baseline["training_id"] for row, baseline in paired
                ),
                "fixed_compiled_sequences_verified": (
                    all(sequence_matches) if len(sequence_matches) == len(paired) else None
                ),
                "difference_from_ideal": layer_changes,
                "difference_percentage_points": {
                    layer: {
                        metric: _stats(
                            [
                                100
                                * (
                                    row["metrics"][layer][metric]
                                    - baseline["metrics"]["ideal"][metric]
                                )
                                for row, baseline in paired
                            ]
                        )
                        for metric in PROBABILITY_METRICS
                    }
                    for layer in ("before_readout", "after_readout")
                },
            }
        )
    preparation_index: dict[str, dict[str, Any]] = {}
    for row in (*good_controls, *good):
        if row.get("relaxation"):
            preparation_index.setdefault(row["reference_id"], row["relaxation"])
    preparations = [
        {
            "reference_id": name,
            "group": references[name]["group"],
            **{
                key: relaxation.get(key)
                for key in (
                    "status",
                    "library_success",
                    "numerically_stationary",
                    "lambda",
                    "numerical_scale",
                    "function_evaluations",
                    "runtime_ms",
                    "projected_gradient_inf",
                    "scaled_projected_gradient_inf",
                    "convex_tangent_gap",
                    "scaled_convex_tangent_gap",
                )
            },
        }
        for name, relaxation in sorted(preparation_index.items())
    ]
    unique_training: dict[str, dict[str, Any]] = {}
    for row in good:
        unique_training.setdefault(row["training_id"], row)
    costs = {
        "unique_preparation_runtime_ms": sum(
            float(row.get("runtime_ms", 0)) for row in preparation_index.values()
        ),
        "unique_training_runtime_ms": sum(
            float(row.get("timings", {}).get("ideal_training_runtime_ms", 0))
            for row in unique_training.values()
        ),
        "unique_compile_runtime_ms": sum(
            float(row.get("timings", {}).get("compile_runtime_ms", 0))
            for row in unique_training.values()
        ),
        **{
            name: sum(float(row.get("timings", {}).get(name, 0)) for row in good)
            for name in ("density_runtime_ms", "readout_runtime_ms", "sampling_analysis_runtime_ms")
        },
    }
    cp_sat = []
    for name, reference in sorted(references.items()):
        result = reference.get("cp_sat_reference")
        if result is None:
            result = next(
                (
                    row["cp_sat_reference"]
                    for row in good_controls
                    if row["reference_id"] == name and row.get("cp_sat_reference")
                ),
                None,
            )
        cp_sat.append({"reference_id": name, "group": reference["group"], "result": result})
    control_rows = [
        {
            "reference_id": row["reference_id"],
            "group": row["group"],
            "control": row["control"],
            "metrics": row["metrics"],
            "sampling": row.get("sampling"),
        }
        for row in sorted(good_controls, key=lambda row: (row["reference_id"], row["control"]))
    ]
    complete = run_audit["status"] == control_audit["status"] == "COMPLETE"
    return {
        "schema_version": 1,
        "artifact_type": "m4_descriptive_summary",
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "manifest_content_sha256": manifest.get("manifest_content_sha256"),
        "config_hash": manifest.get("config_hash"),
        "experiment_counts": {"density_runs": run_audit, "controls": control_audit},
        "aggregation": {
            "primary_metric": "p_schedule_optimal",
            "secondary_metric": "p_ground_encoding",
            "layer_for_primary_comparisons": "after_readout",
            "within_instance": "all planned restarts; median, sample std and range",
            "across_instances": "equal weight per complete instance restart median",
            "paired_statistics": "both difference of medians and median of paired differences",
            "percentage_changes_at_zero_baseline": "undefined; report absolute percentage points",
            "sampling_intervals": "finite sample uncertainty; not problem generalization",
            "partial_groups": "visible but excluded from equal-instance comparisons until complete",
            "significance_claims": False,
        },
        "instance_restart_groups": instance_rows,
        "paired_algorithm_comparisons": pairs,
        "increments_over_classical_initial_distribution": increments,
        "equal_instance_weighted_comparisons": cohorts,
        "fixed_parameter_noise_changes": noise_changes,
        "controls": control_rows,
        "preparations": preparations,
        "preparation_library_converged_count": sum(
            row.get("library_success") is True for row in preparations
        ),
        "preparation_numerically_stationary_count": sum(
            row.get("numerically_stationary") is True for row in preparations
        ),
        "preparation_missing_reference_ids": sorted(set(references) - set(preparation_index)),
        "cp_sat_references": cp_sat,
        "cp_sat_missing_reference_ids": [
            row["reference_id"] for row in cp_sat if row["result"] is None
        ],
        "optimizer_status_counts_unique_training": dict(
            Counter(
                "converged" if row.get("training", {}).get("success") else "nonconverged"
                for row in unique_training.values()
            )
        ),
        "unique_completed_training_count": len(unique_training),
        "recorded_component_costs_ms": costs,
        "cost_accounting": (
            "Preparation and compile/training deduplicated by reference/training ID. Component sum "
            "excludes file I/O, shared gates, controls and harness overhead; campaign wall time is "
            "separate. Per-run standalone includes preparation fully; amortization is explicit."
        ),
        "case_k_exploratory": {
            "selection_reason": "known M3 weakness; exploratory diagnosis, not new hold-out",
            "restart_groups": [
                row for row in instance_rows if row["reference_id"] == "K_bounded_slack"
            ],
            "algorithm_comparisons": [
                row for row in pairs if row["reference_id"] == "K_bounded_slack"
            ],
            "noise_changes": [
                row for row in noise_changes if row["reference_id"] == "K_bounded_slack"
            ],
        },
        "hardware_used": False,
        "training_noise_used": False,
    }


def _finish_plot(figure: Any, path: Path, summary: dict[str, Any]) -> str:
    if summary["status"] != "COMPLETE":
        figure.text(0.5, 0.005, "INCOMPLETE: partial observations", ha="center", color="darkred")
    figure.tight_layout(rect=(0, 0.025, 1, 0.95))
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return str(path)


def _legend(axis: Any) -> None:
    if axis.get_legend_handles_labels()[0]:
        axis.legend(fontsize=8)


def create_plots(summary: dict[str, Any], output: Path) -> list[str]:
    """Five figures with actual per-instance observations and restart spread."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    groups = summary["instance_restart_groups"]
    paths = []
    depths = sorted({row["p"] for row in groups})
    colors = {"standard": "#2369a8", "warm_start": "#b54a20"}
    fig, axes = plt.subplots(1, 2, figsize=(15, 5), squeeze=False)
    for column, dataset in enumerate(DATASETS):
        ax = axes[0, column]
        names = sorted({row["reference_id"] for row in groups if row["group"] == dataset})
        for algorithm in ("standard", "warm_start"):
            for depth_index, p in enumerate(depths):
                rows = [
                    row
                    for row in groups
                    if row["group"] == dataset
                    and row["algorithm"] == algorithm
                    and row["p"] == p
                    and row["noise_profile"] == "ideal"
                ]
                shift = (-0.22 if algorithm == "standard" else 0.03) + depth_index * 0.12
                x = [names.index(row["reference_id"]) + shift for row in rows]
                stats = [row["metrics"]["after_readout"]["p_schedule_optimal"] for row in rows]
                ax.errorbar(
                    x,
                    [s["median"] for s in stats],
                    yerr=[
                        [s["median"] - s["minimum"] for s in stats],
                        [s["maximum"] - s["median"] for s in stats],
                    ],
                    fmt="o" if p == depths[0] else "s",
                    capsize=3,
                    color=colors[algorithm],
                    label=f"{algorithm}, p={p}",
                )
        ax.set_xticks(range(len(names)), names, rotation=45, ha="right", fontsize=8)
        ax.set_title(dataset.replace("_", " "))
        ax.set_ylabel("Ideal schedule-optimal probability")
        ax.set_ylim(-0.02, 1.02)
        ax.grid(axis="y", alpha=0.2)
        _legend(ax)
    fig.suptitle("Standard versus warm QAOA: restart medians and min–max ranges")
    paths.append(_finish_plot(fig, output / "standard_vs_warm_start.png", summary))

    fig, axes = plt.subplots(1, 2, figsize=(15, 5), squeeze=False)
    control_index = {(row["reference_id"], row["control"]): row for row in summary["controls"]}
    for column, dataset in enumerate(DATASETS):
        ax = axes[0, column]
        names = sorted({row["reference_id"] for row in groups if row["group"] == dataset})
        for name in names:
            initial = control_index.get((name, "warm_product"))
            if initial is not None:
                ax.scatter(
                    names.index(name),
                    initial["metrics"]["p_schedule_optimal"],
                    marker="x",
                    s=60,
                    color="#385b35",
                )
        for depth_index, p in enumerate(depths):
            rows = [
                row
                for row in groups
                if row["group"] == dataset
                and row["algorithm"] == "warm_start"
                and row["p"] == p
                and row["noise_profile"] == "ideal"
            ]
            stats = [row["metrics"]["after_readout"]["p_schedule_optimal"] for row in rows]
            ax.errorbar(
                [names.index(row["reference_id"]) + 0.15 + depth_index * 0.13 for row in rows],
                [s["median"] for s in stats],
                yerr=[
                    [s["median"] - s["minimum"] for s in stats],
                    [s["maximum"] - s["median"] for s in stats],
                ],
                fmt="o",
                capsize=3,
                label=f"Warm QAOA, p={p}",
            )
        ax.scatter([], [], marker="x", color="#385b35", label="Classical clipped product, no QAOA")
        ax.set_xticks(range(len(names)), names, rotation=45, ha="right", fontsize=8)
        ax.set_title(dataset.replace("_", " "))
        ax.set_ylabel("Ideal schedule-optimal probability")
        ax.set_ylim(-0.02, 1.02)
        ax.grid(axis="y", alpha=0.2)
        _legend(ax)
    fig.suptitle("Classical warm product versus QAOA layers: median and min–max restart spread")
    paths.append(_finish_plot(fig, output / "warm_product_vs_qaoa.png", summary))

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), squeeze=False)
    profiles = ["ideal", "gate_only", "readout_only", "combined_low", "combined_high"]
    for column, dataset in enumerate(DATASETS):
        for row_index, metric in enumerate(("p_schedule_optimal", "p_ground_encoding")):
            ax = axes[row_index, column]
            keys = sorted(
                {
                    (row["reference_id"], row["p"], row["algorithm"])
                    for row in groups
                    if row["group"] == dataset
                }
            )
            for name, p, algorithm in keys:
                selected = {
                    (row["noise_profile"]): row
                    for row in groups
                    if (row["reference_id"], row["p"], row["algorithm"]) == (name, p, algorithm)
                }
                present = [profile for profile in profiles if profile in selected]
                y = [
                    selected[profile]["metrics"]["after_readout"][metric]["median"]
                    for profile in present
                ]
                ax.plot(
                    [profiles.index(profile) for profile in present],
                    y,
                    color=colors[algorithm],
                    linestyle="-" if p == depths[0] else "--",
                    alpha=0.42,
                    marker="o",
                    markersize=3,
                )
                ax.fill_between(
                    [profiles.index(profile) for profile in present],
                    [
                        selected[profile]["metrics"]["after_readout"][metric]["minimum"]
                        for profile in present
                    ],
                    [
                        selected[profile]["metrics"]["after_readout"][metric]["maximum"]
                        for profile in present
                    ],
                    color=colors[algorithm],
                    alpha=0.035,
                )
            for algorithm, color in colors.items():
                ax.plot([], [], color=color, label=algorithm)
            ax.set_xticks(range(len(profiles)), profiles, rotation=25, ha="right", fontsize=8)
            ax.set_title(f"{dataset.replace('_', ' ')}: {metric}")
            ax.set_ylim(-0.02, 1.02)
            ax.grid(axis="y", alpha=0.2)
            _legend(ax)
    fig.suptitle(
        "Fixed ideal parameters under synthetic noise: each line is an instance/depth median\n"
        "After readout; solid lowest p, dashed larger p; faint bands show restart min–max"
    )
    paths.append(_finish_plot(fig, output / "noise_influence.png", summary))

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), squeeze=False)
    for column, dataset in enumerate(DATASETS):
        ax = axes[0, column]
        for algorithm in ("standard", "warm_start"):
            rows = [
                row
                for row in groups
                if row["group"] == dataset
                and row["algorithm"] == algorithm
                and row["noise_profile"] in ("ideal", "combined_high")
            ]
            for profile, marker in (("ideal", "o"), ("combined_high", "x")):
                subset = [row for row in rows if row["noise_profile"] == profile]
                encoding = [
                    row["metrics"]["after_readout"]["p_encoding_feasible"] for row in subset
                ]
                schedule = [
                    row["metrics"]["after_readout"]["p_schedule_feasible"] for row in subset
                ]
                ax.errorbar(
                    [
                        row["metrics"]["after_readout"]["p_encoding_feasible"]["median"]
                        for row in subset
                    ],
                    [
                        row["metrics"]["after_readout"]["p_schedule_feasible"]["median"]
                        for row in subset
                    ],
                    color=colors[algorithm],
                    xerr=[
                        [s["median"] - s["minimum"] for s in encoding],
                        [s["maximum"] - s["median"] for s in encoding],
                    ],
                    yerr=[
                        [s["median"] - s["minimum"] for s in schedule],
                        [s["maximum"] - s["median"] for s in schedule],
                    ],
                    fmt=marker,
                    alpha=0.55,
                    linestyle="none",
                    capsize=2,
                    label=f"{algorithm}, {profile}",
                )
        ax.plot([0, 1], [0, 1], color="gray", linestyle=":", label="Equal feasibility")
        ax.set(
            xlim=(-0.02, 1.02),
            ylim=(-0.02, 1.02),
            xlabel="Encoding-feasible probability",
            ylabel="Decoded schedule-feasible probability",
            title=dataset.replace("_", " "),
        )
        _legend(ax)
        ax.grid(alpha=0.2)
    fig.suptitle(
        "Encoding versus decoded schedule feasibility: instance/depth medians, min–max ranges"
    )
    paths.append(_finish_plot(fig, output / "encoding_vs_schedule_feasibility.png", summary))

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), squeeze=False)
    for column, metric in enumerate(("p_schedule_optimal", "p_ground_encoding")):
        ax = axes[0, column]
        for algorithm in ("standard", "warm_start"):
            for p in depths:
                selected = {
                    row["noise_profile"]: row
                    for row in groups
                    if row["reference_id"] == "K_bounded_slack"
                    and row["algorithm"] == algorithm
                    and row["p"] == p
                }
                present = [profile for profile in profiles if profile in selected]
                stats = [
                    selected[profile]["metrics"]["after_readout"][metric] for profile in present
                ]
                ax.errorbar(
                    [profiles.index(profile) for profile in present],
                    [s["median"] for s in stats],
                    yerr=[
                        [s["median"] - s["minimum"] for s in stats],
                        [s["maximum"] - s["median"] for s in stats],
                    ],
                    fmt="o-" if p == depths[0] else "s--",
                    capsize=3,
                    color=colors[algorithm],
                    label=f"{algorithm}, p={p}",
                )
        for control, style in (("uniform", ":"), ("warm_product", "-.")):
            row = control_index.get(("K_bounded_slack", control))
            if row is not None:
                ax.axhline(
                    row["metrics"][metric],
                    color="gray",
                    linestyle=style,
                    label=f"{control}: ideal classical control",
                )
        ax.set_xticks(range(len(profiles)), profiles, rotation=25, ha="right", fontsize=8)
        ax.set_ylabel(metric)
        ax.set_ylim(bottom=0)
        _legend(ax)
        ax.grid(axis="y", alpha=0.2)
    fig.suptitle(
        "Case K: explicitly exploratory known weakness; median and min–max restarts\n"
        "Synthetic registered probabilities; classical control lines remain ideal"
    )
    paths.append(_finish_plot(fig, output / "case_k_exploratory.png", summary))
    return paths
