"""Independent arithmetic fixtures for honest restart/instance reporting."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from km_quantum.m4.reporting import create_plots, summarize_records


def _quality(optimal: float) -> dict[str, Any]:
    return {
        "p_schedule_optimal": optimal,
        "p_ground_encoding": optimal / 2,
        "p_schedule_feasible": 0.3 + 0.7 * optimal,
        "p_encoding_feasible": 0.2 + 0.5 * optimal,
        "expected_qubo_energy": 100 * (1 - optimal),
        "normalized_energy_error": 1 - optimal,
        "conditional_valid_m1_objective": 2.0,
    }


@pytest.fixture
def fixture() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    profiles = ({"name": "ideal"}, {"name": "combined_high"})
    references = [
        {
            "reference_id": name,
            "group": group,
            "qubit_count": 3,
            "cp_sat_reference": {"status": "OPTIMAL", "objective_value": 0, "runtime_ms": 0.5},
        }
        for name, group in (
            ("X", "known_diagnostic"),
            ("Y", "known_diagnostic"),
            ("Z", "new_evaluation"),
        )
    ]
    angular = {
        "X": {"standard": [0, 0.4, 0.5], "warm_start": [0.3, 0.2, 0.6]},
        "Y": {"standard": [0.8, 0.8, 0.8], "warm_start": [0.9, 0.9, 0.9]},
        "Z": {"standard": [0.05, 0.15, 0.2], "warm_start": [0.02, 0.25, 0.3]},
    }
    planned, records, planned_controls, controls = [], [], [], []
    relaxation = {
        "status": "CONVERGED",
        "library_success": True,
        "numerically_stationary": False,
        "lambda": 5.0,
        "numerical_scale": 20.0,
        "function_evaluations": 4,
        "runtime_ms": 2.0,
        "projected_gradient_inf": 1e-4,
        "scaled_projected_gradient_inf": 5e-6,
        "convex_tangent_gap": 0.001,
        "scaled_convex_tangent_gap": 0.00005,
    }
    for reference in references:
        name, group = reference["reference_id"], reference["group"]
        for algorithm in ("standard", "warm_start"):
            for seed, probability in enumerate(angular[name][algorithm]):
                training_id = f"{name}-{algorithm}-p1-seed{seed}"
                for profile in profiles:
                    run_id = f"{training_id}-{profile['name']}"
                    common = {
                        "run_id": run_id,
                        "training_id": training_id,
                        "reference_id": name,
                        "algorithm": algorithm,
                        "p": 1,
                        "initialization_seed": seed,
                        "noise_profile": profile,
                    }
                    planned.append(common)
                    # Synthetic test arithmetic deliberately includes improvement under noise.
                    change = 0.02 if name == "Y" else -0.02
                    observed = (
                        probability if profile["name"] == "ideal" else max(0, probability + change)
                    )
                    before = (
                        probability
                        if profile["name"] == "ideal"
                        else max(0, probability + change / 2)
                    )
                    records.append(
                        {
                            **common,
                            "group": group,
                            "status": "COMPLETE",
                            "metrics": {
                                "ideal": _quality(probability),
                                "before_readout": _quality(before),
                                "after_readout": _quality(observed),
                            },
                            "relaxation": relaxation,
                            "training": {"nfev": 20, "success": seed == 0},
                            "compiler": {"sequence_sha256": training_id},
                            "timings": {
                                "ideal_training_runtime_ms": 10.0,
                                "compile_runtime_ms": 3.0,
                                "density_runtime_ms": 6.0,
                                "readout_runtime_ms": 0.1,
                                "sampling_analysis_runtime_ms": 0.2,
                                "distribution_analysis_runtime_ms": 0.3,
                                "standalone_end_to_end_ms": 21.3,
                                "amortized_end_to_end_ms": 20.3,
                            },
                        }
                    )
        for control in ("uniform", "rounded_relaxation", "warm_product"):
            identity = {"control_id": f"{name}-{control}", "reference_id": name, "control": control}
            planned_controls.append(identity)
            probability = 0.01 if control == "uniform" else 0.4 if control == "warm_product" else 0
            values = _quality(probability)
            if control == "rounded_relaxation":
                values.update(
                    p_schedule_feasible=0,
                    p_encoding_feasible=0,
                    conditional_valid_m1_objective=None,
                )
            controls.append(
                {
                    **identity,
                    "group": group,
                    "status": "COMPLETE",
                    "metrics": values,
                    "relaxation": relaxation,
                }
            )
    manifest = {
        "manifest_content_sha256": "f" * 64,
        "config_hash": "c" * 64,
        "references": references,
        "planned_noise_runs": planned,
        "planned_control_runs": planned_controls,
    }
    return records, controls, manifest


def _group(
    summary: dict[str, Any], name: str, algorithm: str, profile: str = "ideal"
) -> dict[str, Any]:
    return next(
        row
        for row in summary["instance_restart_groups"]
        if (row["reference_id"], row["algorithm"], row["noise_profile"])
        == (name, algorithm, profile)
    )


def test_exact_restart_statistics_and_group_separation(
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    summary = summarize_records(*fixture)
    assert summary["status"] == "COMPLETE"
    stats = _group(summary, "X", "standard")["metrics"]["after_readout"]["p_schedule_optimal"]
    assert stats["median"] == 0.4
    assert stats["mean"] == pytest.approx(0.3)
    assert stats["sample_std"] == pytest.approx(0.2645751311)
    assert stats["minimum"] == 0 and stats["maximum"] == 0.5 and stats["count"] == 3
    cohort = next(
        row
        for row in summary["equal_instance_weighted_comparisons"]
        if row["group"] == "known_diagnostic" and row["noise_profile"] == "ideal"
    )
    assert cohort["algorithms"]["standard"]["instance_count"] == 2
    stats = cohort["algorithms"]["standard"]["instance_median_statistics"]["p_schedule_optimal"]
    assert stats["median"] == pytest.approx(0.6)
    assert stats["mean"] == pytest.approx(0.6)
    assert cohort["algorithms"]["standard"]["reference_ids"] == ["X", "Y"]


def test_median_of_paired_differences_is_separate_from_difference_of_medians(
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    summary = summarize_records(*fixture)
    pair = next(
        row
        for row in summary["paired_algorithm_comparisons"]
        if row["reference_id"] == "X" and row["noise_profile"] == "ideal"
    )
    assert pair["difference_of_restart_medians_warm_minus_standard"][
        "p_schedule_optimal"
    ] == pytest.approx(-0.1)
    assert pair["paired_restart_differences_warm_minus_standard"]["p_schedule_optimal"][
        "median"
    ] == pytest.approx(0.1)
    assert pair["paired_restart_differences_percentage_points"]["p_schedule_optimal"][
        "median"
    ] == pytest.approx(10.0)
    cohort = next(
        row
        for row in summary["equal_instance_weighted_comparisons"]
        if row["group"] == "known_diagnostic" and row["noise_profile"] == "ideal"
    )
    assert cohort["instance_difference_of_medians"]["p_schedule_optimal"]["mean"] == pytest.approx(
        0
    )
    assert cohort["instance_median_of_paired_restart_differences"]["p_schedule_optimal"][
        "mean"
    ] == pytest.approx(0.1)


def test_increments_separate_classical_start_from_qaoa(
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    summary = summarize_records(*fixture)
    row = next(
        row
        for row in summary["increments_over_classical_initial_distribution"]
        if row["reference_id"] == "X"
        and row["algorithm"] == "warm_start"
        and row["noise_profile"] == "ideal"
    )
    assert row["initial_control"] == "warm_product"
    assert row["difference_percentage_points"]["p_schedule_optimal"] == pytest.approx(-10.0)
    standard = next(
        row
        for row in summary["increments_over_classical_initial_distribution"]
        if row["reference_id"] == "X"
        and row["algorithm"] == "standard"
        and row["noise_profile"] == "ideal"
    )
    assert standard["difference_percentage_points"]["p_schedule_optimal"] == pytest.approx(39.0)


def test_noise_changes_allow_positive_negative_and_neutral_metrics(
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    summary = summarize_records(*fixture)
    rows = {
        row["reference_id"]: row
        for row in summary["fixed_parameter_noise_changes"]
        if row["algorithm"] == "standard"
    }
    assert rows["Y"]["difference_percentage_points"]["after_readout"]["p_schedule_optimal"][
        "median"
    ] == pytest.approx(2.0)
    assert rows["Z"]["difference_percentage_points"]["before_readout"]["p_schedule_optimal"][
        "median"
    ] == pytest.approx(-1.0)
    assert rows["Z"]["difference_percentage_points"]["after_readout"]["p_schedule_optimal"][
        "median"
    ] == pytest.approx(-2.0)
    assert all(
        row["fixed_training_ids_verified"] and row["fixed_compiled_sequences_verified"]
        for row in rows.values()
    )


def test_preparation_cost_is_deduplicated_and_stationarity_flags_honest(
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    summary = summarize_records(*fixture)
    assert len(summary["preparations"]) == summary["preparation_library_converged_count"] == 3
    assert summary["preparation_numerically_stationary_count"] == 0
    assert summary["unique_completed_training_count"] == 18
    costs = summary["recorded_component_costs_ms"]
    assert costs["unique_preparation_runtime_ms"] == 6
    assert costs["unique_training_runtime_ms"] == 180
    assert costs["unique_compile_runtime_ms"] == 54
    assert costs["density_runtime_ms"] == 216
    assert costs["distribution_analysis_runtime_ms"] == pytest.approx(10.8)
    assert not summary["cp_sat_missing_reference_ids"]
    assert summary["optimizer_status_counts_unique_training"] == {
        "converged": 6,
        "nonconverged": 12,
    }


@pytest.mark.parametrize(
    "problem", ["missing", "failed", "skipped", "duplicate", "unexpected", "invalid"]
)
def test_partial_and_failed_records_remain_visible(
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]], problem: str
) -> None:
    records, controls, manifest = deepcopy(fixture)
    if problem == "missing":
        records.pop(0)
    elif problem == "failed":
        records[0].update(status="FAILED", reason="fixture failure")
    elif problem == "skipped":
        records[0].update(status="SKIPPED", reason="resource limit")
    elif problem == "duplicate":
        records.append(deepcopy(records[0]))
    elif problem == "unexpected":
        records.append({**records[0], "run_id": "unexpected-id"})
    else:
        records[0]["metrics"]["after_readout"]["p_schedule_optimal"] = float("nan")
    summary = summarize_records(records, controls, manifest)
    assert summary["status"] == "INCOMPLETE"
    audit = summary["experiment_counts"]["density_runs"]
    category = (
        "skipped_or_aborted"
        if problem == "skipped"
        else "duplicates"
        if problem == "duplicate"
        else problem
    )
    assert audit[category]
    assert audit["status"] == "INCOMPLETE"
    if problem in ("missing", "failed", "skipped", "invalid"):
        cohort = next(
            row
            for row in summary["equal_instance_weighted_comparisons"]
            if row["group"] == "known_diagnostic" and row["noise_profile"] == "ideal"
        )
        assert cohort["paired_instance_count"] == 1
        assert cohort["algorithms"]["standard"]["reference_ids"] == ["Y"]


def test_missing_control_is_explicit_and_invalid_rounding_keeps_null_objective(
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    records, controls, manifest = fixture
    summary = summarize_records(records, controls[1:], manifest)
    assert summary["status"] == "INCOMPLETE"
    assert summary["experiment_counts"]["controls"]["missing"] == ["X-uniform"]
    assert _group(summary, "X", "standard")["ground_lift_after_readout"] is None
    rounded = next(row for row in summary["controls"] if row["control"] == "rounded_relaxation")
    assert rounded["metrics"]["conditional_valid_m1_objective"] is None


def test_deterministic_summary_ignores_input_row_order(
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    records, controls, manifest = fixture
    assert summarize_records(records, controls, manifest) == summarize_records(
        list(reversed(records)), list(reversed(controls)), manifest
    )


def test_observational_timing_scope_text_is_preserved_without_numeric_conversion(
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    records, controls, manifest = fixture
    for row in records:
        row["timings"]["scope"] = "Standalone includes preparation; campaign is separate"
    summary = summarize_records(records, controls, manifest)
    assert summary["status"] == "COMPLETE"
    assert "scope" not in _group(summary, "X", "standard")["timings_ms"]


def test_all_five_plots_use_actual_summary_values(
    tmp_path: Path,
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    records, controls, manifest = fixture
    # Give the existing numeric diagnostic fixture the required explicitly exploratory label.
    for row in (
        *records,
        *controls,
        *manifest["planned_noise_runs"],
        *manifest["planned_control_runs"],
    ):
        if row["reference_id"] == "X":
            row["reference_id"] = "K_bounded_slack"
    manifest["references"][0]["reference_id"] = "K_bounded_slack"
    summary = summarize_records(records, controls, manifest)
    assert summary["status"] == "COMPLETE"
    paths = create_plots(summary, tmp_path)
    assert len(paths) == 5 and len(set(paths)) == 5
    assert all(Path(path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in paths)
    assert all(Path(path).stat().st_size > 10000 for path in paths)
    assert summary["case_k_exploratory"]["restart_groups"]


def test_incomplete_empty_suite_plotting_is_explicit(
    tmp_path: Path,
    fixture: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]],
) -> None:
    _, _, manifest = fixture
    summary = summarize_records([], [], manifest)
    assert summary["status"] == "INCOMPLETE"
    assert summary["experiment_counts"]["density_runs"]["completed"] == 0
    assert len(create_plots(summary, tmp_path)) == 5
