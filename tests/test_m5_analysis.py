"""Valid hardware deviations, literal M2 semantics and persistent PUB identity."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from km_quantum.m5.analysis import (
    analyze_counts,
    analyze_results,
    decode_register_counts,
    histogram_shot_effect,
)
from km_quantum.qaoa.references import FrozenReference, load_references

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def references() -> dict[str, FrozenReference]:
    return load_references(ROOT)


def test_arbitrarily_bad_hardware_agreement_is_data_not_sampler_failure(
    references: dict[str, FrozenReference],
) -> None:
    report = analyze_counts(
        references["A_single_job"],
        {"00": 1024},
        shots=1024,
        predictions={"original_ideal": [0, 1, 0, 0], "device_observed": [0.05, 0.8, 0.1, 0.05]},
    )
    assert report["analysis_status"] == "ANALYZED"
    assert report["hardware_used"]
    assert not report["m5_pass"]
    assert "sampling_verified" not in report
    assert report["comparisons"]["original_ideal"]["total_variation_distance"] == 1
    assert report["best_valid_sample_plan"] is None
    assert report["best_valid_sample_objective"] is None
    assert report["best_valid_sample_objective_gap"] is None
    assert report["empirical"]["conditional_valid_m1_objective"] is None
    assert report["event_intervals"]["p_schedule_optimal"]["confidence_interval_95"][1] > 0
    assert all(
        plan["m1_objective"] is None
        for plan in report["empirical"]["decoded_plans"]
        if not plan["schedule_feasible"]
    )


def test_nonidentity_classical_mapping_uses_asymmetric_bits_and_preserves_raw_zero_counts() -> None:
    # classical positions c0,c1,c2,c3 = 1,0,0,1; M2 reads c3,c0,c2 -> 110.
    decoded, integer_counts = decode_register_counts(
        {"1001": 128, "0000": 0}, shots=128, m2_to_classical=(3, 0, 2), classical_width=4
    )
    assert decoded == {"000": 0, "110": 128}
    assert integer_counts[3] == 128
    assert integer_counts.sum() == 128


def test_auxiliary_bits_energy_and_application_objective_reuse_frozen_metrics(
    references: dict[str, FrozenReference],
) -> None:
    report = analyze_counts(
        references["H_workload"],
        {"10110": 1024},
        shots=1024,
        predictions={"compiled_ideal": [1 / 32] * 32},
    )
    assert report["m2_decoded_counts"] == {"01101": 1024}
    assert report["empirical"]["p_schedule_optimal"] == 1
    assert report["empirical"]["p_ground_encoding"] == 0
    assert report["empirical_mean_qubo_energy_with_offset"] == 22
    assert report["best_valid_sample_objective"] == 7
    assert report["best_valid_sample_objective_gap"] == 0
    assert report["best_valid_sample_plan"]["solver_metadata"]["repair_applied"] is False
    assert not report["postselection_applied"]


@pytest.mark.parametrize(
    "counts,shots", [({"00": -1}, 1), ({"00": True}, 1), ({"00": 2}, 3), ({}, 1), ({"000": 1}, 1)]
)
def test_invalid_counts_are_rejected(
    references: dict[str, FrozenReference], counts: dict[str, int], shots: int
) -> None:
    with pytest.raises(ValueError):
        analyze_counts(references["A_single_job"], counts, shots=shots, predictions={})


def test_shot_effect_is_prespecified_deterministic_and_not_model_validation() -> None:
    first = histogram_shot_effect([0.1, 0.2, 0.3, 0.4], shots=1024)
    assert first == histogram_shot_effect([0.1, 0.2, 0.3, 0.4], shots=1024)
    assert first["multinomial_replicates"] == 1000
    assert first["quantiles"]["0.025"] < first["quantiles"]["0.975"]
    assert not first["model_truth_confirmed"]
    assert not first["hardware_randomness_controlled"]
    assert histogram_shot_effect([0, 1], shots=128)["quantiles"] == {
        "0.025": 0,
        "0.5": 0,
        "0.975": 0,
    }


def _pubs() -> list[dict[str, Any]]:
    return [
        {
            "pub_id": "block1-c1",
            "circuit_id": "c1",
            "circuit_sha256": "a" * 64,
            "case_name": "A_single_job",
            "block": 1,
            "shots": 1024,
        },
        {
            "pub_id": "block2-c1",
            "circuit_id": "c1",
            "circuit_sha256": "a" * 64,
            "case_name": "A_single_job",
            "block": 2,
            "shots": 1024,
        },
    ]


def _raw(pub: dict[str, Any], *, source: str = "hardware") -> dict[str, Any]:
    return {
        **pub,
        "counts": {"00": 1024},
        "source": source,
        "hardware_used": source == "hardware",
    }


def test_no_hardware_results_is_not_available_and_never_m5_pass(
    references: dict[str, FrozenReference],
) -> None:
    report = analyze_results(_pubs(), [], {}, references)
    assert report["analysis_status"] == "NOT_AVAILABLE"
    assert report["missing_pub_ids"] == ["block1-c1", "block2-c1"]
    assert report["observed_hardware_agreement"] == "NOT_AVAILABLE"
    assert not report["hardware_used"] and not report["m5_pass"]


def test_missing_pub_and_blocks_kept_separate_with_persistent_ids(
    references: dict[str, FrozenReference],
) -> None:
    pubs = _pubs()
    predictions = {"c1": {"device_observed": [1, 0, 0, 0]}}
    partial = analyze_results(pubs, [_raw(pubs[1])], predictions, references)
    assert partial["analysis_status"] == "PARTIAL"
    assert partial["missing_pub_ids"] == ["block1-c1"]
    # Input order is intentionally reversed, but result IDs retain block identity.
    complete = analyze_results(pubs, [_raw(pubs[1]), _raw(pubs[0])], predictions, references)
    assert complete["analysis_status"] == "ANALYZED"
    assert complete["blocks"]["1"][0]["pub_id"] == "block1-c1"
    assert complete["blocks"]["2"][0]["pub_id"] == "block2-c1"
    assert not complete["blocks_pooled"]
    assert not complete["m5_pass"]


def test_mock_results_cannot_appear_as_hardware_pass(
    references: dict[str, FrozenReference],
) -> None:
    pubs = _pubs()
    report = analyze_results(
        pubs,
        [_raw(pub, source="mock") for pub in pubs],
        {"c1": {"compiled_ideal": [0.25] * 4}},
        references,
    )
    assert report["analysis_status"] == "TEST_DATA_ONLY"
    assert not report["hardware_used"] and not report["m5_pass"]
    assert report["observed_hardware_agreement"] == "NOT_AVAILABLE"
    for block in report["blocks"].values():
        assert block[0]["artifact_type"] == "m5_test_count_analysis"


@pytest.mark.parametrize(
    "mutation", ["wrong_pub", "wrong_circuit", "wrong_hash", "missing_shots", "mock_hardware"]
)
def test_persistent_result_contract_errors(
    references: dict[str, FrozenReference], mutation: str
) -> None:
    pubs = _pubs()
    raw = _raw(pubs[0])
    if mutation == "wrong_pub":
        raw["pub_id"] = "unknown"
    elif mutation == "wrong_circuit":
        raw["circuit_id"] = "c2"
    elif mutation == "wrong_hash":
        raw["circuit_sha256"] = "b" * 64
    elif mutation == "missing_shots":
        raw["shots"] = 1023
    else:
        raw["source"] = "mock"
    with pytest.raises(ValueError):
        analyze_results(pubs, [raw], {"c1": {"original_ideal": [0.25] * 4}}, references)


def test_probability_comparison_percentage_points_and_raw_histograms(
    references: dict[str, FrozenReference],
) -> None:
    counts = {"00": 512, "01": 512, "10": 0}
    report = analyze_counts(
        references["A_single_job"],
        counts,
        shots=1024,
        predictions={"original_ideal": [0.25] * 4},
    )
    assert report["raw_counts"] == counts
    assert report["m2_decoded_counts"] == {"00": 512, "01": 0, "10": 512}
    assert report["comparisons"]["original_ideal"]["total_variation_distance"] == 0.5
    row = report["comparisons"]["original_ideal"]["metric_differences"]["p_schedule_feasible"]
    assert row["signed_percentage_points"] == 25
    assert row["absolute_percentage_points"] == 25
    np.testing.assert_allclose(report["empirical"]["p_schedule_feasible"], 0.5)
