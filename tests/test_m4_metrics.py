"""Application/encoding semantics, plan aggregation and finite sampling checks."""

from pathlib import Path

import numpy as np
import pytest

from km_quantum.m4.metrics import (
    analyze_distribution,
    build_plan_table,
    create_metric_semantics_audit,
    sample_metrics,
)
from km_quantum.qaoa.ordering import m2_bits_to_qiskit_bitstring
from km_quantum.qaoa.references import FrozenReference, load_references

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def references() -> dict[str, FrozenReference]:
    return load_references(ROOT)


def test_wrong_slack_decodes_optimal_but_is_not_ground(
    references: dict[str, FrozenReference],
) -> None:
    ref = references["H_workload"]
    table = build_plan_table(ref)
    values = np.zeros(32)
    values[table.state_table.bits.index("01101")] = 1
    report = analyze_distribution(ref, table, values)
    assert report["p_schedule_optimal"] == report["p_schedule_feasible"] == 1
    assert report["p_ground_encoding"] == report["p_encoding_feasible"] == 0
    assert report["expected_qubo_energy"] == 22
    assert report["conditional_valid_m1_objective"] == 7
    assert report["conditional_objective_feasibility_rate"] == 1


def test_zero_hard_penalty_does_not_make_imbalance_auxiliaries_minimal(
    references: dict[str, FrozenReference],
) -> None:
    ref = references["M_active_imbalance"]
    table = build_plan_table(ref)
    values = np.zeros(1024)
    values[table.state_table.bits.index("0011011010")] = 1
    report = analyze_distribution(ref, table, values)
    assert report["p_encoding_feasible"] == report["p_schedule_feasible"] == 1
    assert report["expected_qubo_energy"] == 9
    assert report["conditional_valid_m1_objective"] == 7
    assert report["p_ground_encoding"] == report["p_schedule_optimal"] == 0


def test_plan_aggregation_preserves_all_degenerate_slack_representations(
    references: dict[str, FrozenReference],
) -> None:
    ref = references["K_bounded_slack"]
    table = build_plan_table(ref)
    values = np.full(128, 1 / 128)
    report = analyze_distribution(ref, table, values)
    assert len(table.plans) == 16
    assert report["ground_decoded_plan_count"] == 1
    assert report["ground_auxiliary_degeneracy"][0]["ground_encoding_multiplicity"] == 2
    assert report["maximum_encoding_multiplicity_per_plan"] == 8
    assert report["p_ground_encoding"] == 2 / 128
    assert report["p_schedule_optimal"] == 8 / 128
    assert sum(row["probability"] for row in report["decoded_plans"]) == 1
    assert all(row["m1_objective"] is None for row in table.plans if not row["schedule_feasible"])
    assert not table.state_to_plan.flags.writeable


@pytest.mark.parametrize(
    "name", ["A_single_job", "H_workload", "K_bounded_slack", "M_active_imbalance"]
)
def test_random_distribution_containment_and_conditional_objective(
    references: dict[str, FrozenReference], name: str
) -> None:
    ref = references[name]
    table = build_plan_table(ref)
    probabilities = np.random.default_rng(17).dirichlet(np.ones(len(table.state_table.bits)))
    r = analyze_distribution(ref, table, probabilities)
    assert r["p_ground_encoding"] <= r["p_schedule_optimal"] <= r["p_schedule_feasible"]
    assert r["p_encoding_feasible"] <= r["p_schedule_feasible"]
    numerator = sum(
        row["probability"] * row["m1_objective"]
        for row in r["decoded_plans"]
        if row["schedule_feasible"]
    )
    assert r["conditional_valid_m1_objective"] == pytest.approx(
        numerator / r["p_schedule_feasible"]
    )


def test_sampled_optimal_plan_with_wrong_auxiliary_is_application_success(
    references: dict[str, FrozenReference],
) -> None:
    ref = references["H_workload"]
    table = build_plan_table(ref)
    values = np.zeros(32)
    values[table.state_table.bits.index("01101")] = 1
    counts = {m2_bits_to_qiskit_bitstring("01101"): 1024}
    report = sample_metrics(ref, table, counts, shots=1024, seed=8, probabilities=values)
    assert report["sampling_verified"]
    assert report["sampling_intervals"]["p_schedule_optimal"]["frequency"] == 1
    assert report["sampling_intervals"]["p_ground_encoding"]["frequency"] == 0
    assert report["best_valid_sample_objective"] == 7
    assert report["best_valid_sample_objective_gap"] == 0
    assert report["best_valid_sample_plan"]["solver_metadata"]["repair_applied"] is False
    assert report["distinct_sampled_decoded_plans"] == 1


def test_invalid_sample_has_null_objective_gap_and_no_invented_plan(
    references: dict[str, FrozenReference],
) -> None:
    ref = references["A_single_job"]
    table = build_plan_table(ref)
    values = np.zeros(4)
    values[0] = 1
    report = sample_metrics(ref, table, {"00": 32}, shots=32, seed=0, probabilities=values)
    assert report["best_valid_sample_plan"] is None
    assert report["best_valid_sample_objective"] is None
    assert report["best_valid_sample_objective_gap"] is None
    assert report["empirical"]["conditional_valid_m1_objective"] is None
    assert report["valid_sample_frequency"] == 0


def test_full_seeded_distribution_sampling_is_stable(
    references: dict[str, FrozenReference],
) -> None:
    ref = references["A_single_job"]
    table = build_plan_table(ref)
    probabilities = np.asarray([0.1, 0.4, 0.2, 0.3])
    sampled = np.random.default_rng(123).multinomial(4096, probabilities)
    counts = {format(i, "02b"): int(count) for i, count in enumerate(sampled) if count}
    report = sample_metrics(ref, table, counts, shots=4096, seed=123, probabilities=probabilities)
    assert report["sampling_verified"]
    row = report["sampling_intervals"]["p_schedule_feasible"]
    assert row["confidence_interval_95"][0] < probabilities[1] < row["confidence_interval_95"][1]


@pytest.mark.parametrize(
    "counts,shots", [({"00": True}, 1), ({"00": -1}, 1), ({}, 1), ({"00": 2}, 3), ({"000": 1}, 1)]
)
def test_sample_input_errors(
    references: dict[str, FrozenReference], counts: dict[str, int], shots: int
) -> None:
    ref = references["A_single_job"]
    table = build_plan_table(ref)
    with pytest.raises(ValueError):
        sample_metrics(ref, table, counts, shots=shots, seed=0, probabilities=[0.25] * 4)


def test_distribution_input_errors(references: dict[str, FrozenReference]) -> None:
    ref = references["A_single_job"]
    table = build_plan_table(ref)
    with pytest.raises(ValueError, match="another"):
        analyze_distribution(references["H_workload"], table, [0.25] * 4)
    for values in ([1], [0.1] * 4, [float("nan"), 0, 0, 1], [-0.1, 0.1, 0, 1]):
        with pytest.raises(ValueError, match="probabilit"):
            analyze_distribution(ref, table, values)


def test_historical_m3_semantics_are_versioned_without_correction() -> None:
    audit = create_metric_semantics_audit(ROOT)
    assert audit["status"] == "PASS"
    assert audit["checked_basis_states"] == 47014
    assert audit["checked_frozen_references"] == 114
    assert audit["m3_saved_rows"] == 660
    assert audit["m3_rows_decoded_optimal_exceeds_ground"] == 240
    assert audit["historical_m3_correction_required"] is False
    assert audit["historical_m3_files_modified"] is False
    strong = audit["counterexamples"]["zero_penalty_optimal_schedule_nonground"]
    assert strong["metrics_for_point_mass"]["p_encoding_feasible"] == 1
    assert strong["metrics_for_point_mass"]["p_schedule_optimal"] == 1
    assert strong["metrics_for_point_mass"]["p_ground_encoding"] == 0
