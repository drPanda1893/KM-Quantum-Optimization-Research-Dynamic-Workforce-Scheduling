"""Prescribed offline 512/1024 precision, event semantics and no network use."""

import json
import math
import socket
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scipy.stats import beta

from km_quantum.m4.metrics import build_plan_table
from km_quantum.m5 import budget_precision
from km_quantum.m5.analysis import histogram_shot_effect
from km_quantum.m5.budget_precision import (
    hypothetical_hit_interval,
    precision_from_distribution,
    precision_from_metric_probabilities,
)
from km_quantum.qaoa.ordering import m2_bits_to_basis_index
from km_quantum.qaoa.references import FrozenReference, load_references

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Offline precision calculations must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.fixture(scope="module")
def references() -> dict[str, FrozenReference]:
    return load_references(ROOT)


def _metrics(probability: float) -> dict[str, float]:
    return dict.fromkeys(budget_precision.METRIC_MASKS, probability)


def _distribution_report(reference: FrozenReference, values: Any) -> dict[str, Any]:
    return precision_from_distribution(
        reference,
        values,
        circuit_id="frozen-circuit",
        prediction_label="device_noise_post_readout",
        prediction_reference={"path": "archived/prediction.json", "sha256": "a" * 64},
    )


def test_worst_case_standard_errors_and_nonzero_variance_ratios() -> None:
    report = precision_from_metric_probabilities(_metrics(0.5))
    for row in report["metrics"].values():
        assert row["by_shots"]["1024"]["standard_error"] == 0.015625
        assert row["by_shots"]["512"]["standard_error"] == pytest.approx(0.02209708691207961)
        assert row["by_shots"]["512"]["standard_error_percentage_points"] == pytest.approx(
            2.209708691207961
        )
        assert row["standard_error_ratio_512_over_1024"] == math.sqrt(2)
        assert row["variance_ratio_512_over_1024"] == 2
        assert row["by_shots"]["512"]["variance"] == 2 * row["by_shots"]["1024"]["variance"]
    assert (
        report["worst_case_bernoulli_precision"]["by_shots"]["1024"][
            "standard_error_percentage_points"
        ]
        == 1.5625
    )


@pytest.mark.parametrize("probability", [0.0001, 0.05, 0.9])
def test_formula_uses_each_actual_shot_count(probability: float) -> None:
    row = precision_from_metric_probabilities(_metrics(probability))["metrics"]["p_ground_encoding"]
    for shots in (512, 1024):
        detail = row["by_shots"][str(shots)]
        assert detail["standard_error"] == math.sqrt(probability * (1 - probability) / shots)
        assert detail["expected_hits_if_reference_is_true"] == probability * shots
        assert detail["probability_of_zero_hits_if_reference_is_true"] == pytest.approx(
            (1 - probability) ** shots
        )


@pytest.mark.parametrize("probability", [0.0, 1.0])
def test_deterministic_reference_has_no_undefined_numeric_ratios(probability: float) -> None:
    report = precision_from_metric_probabilities(_metrics(probability))
    for row in report["metrics"].values():
        assert not row["ratio_defined"]
        assert row["standard_error_ratio_512_over_1024"] is None
        assert row["variance_ratio_512_over_1024"] is None
        for detail in row["by_shots"].values():
            assert detail["variance"] == detail["standard_error"] == 0
            assert detail["probability_of_zero_hits_if_reference_is_true"] == 1 - probability
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("shots", [512, 1024])
@pytest.mark.parametrize("hits", [0, 1, 2])
def test_hypothetical_rare_hit_intervals_are_exact_two_sided(shots: int, hits: int) -> None:
    interval = hypothetical_hit_interval(hits, shots=shots)
    lower, upper = interval["confidence_interval_95"]
    assert lower == pytest.approx(0 if hits == 0 else beta.ppf(0.025, hits, shots - hits + 1))
    assert upper == pytest.approx(beta.ppf(0.975, hits + 1, shots - hits))
    assert interval["outcome_kind"] == "HYPOTHETICAL"
    assert not interval["observed"]
    assert not interval["zero_hits_implies_zero_probability"]
    assert interval["shots"] == shots
    assert interval["hypothetical_frequency"] == hits / shots


def test_zero_hits_never_imply_probability_zero_and_smaller_n_is_less_precise() -> None:
    upper_512 = hypothetical_hit_interval(0, shots=512)["confidence_interval_95"][1]
    upper_1024 = hypothetical_hit_interval(0, shots=1024)["confidence_interval_95"][1]
    assert upper_512 == pytest.approx(1 - 0.025 ** (1 / 512))
    assert upper_1024 == pytest.approx(1 - 0.025 ** (1 / 1024))
    assert upper_512 > upper_1024 > 0


@pytest.mark.parametrize(
    "hits,shots", [(-1, 512), (513, 512), (True, 512), (0, True), (0, 128), (0, 512.0)]
)
def test_invalid_hypothetical_interval_inputs_are_rejected(hits: Any, shots: Any) -> None:
    with pytest.raises(ValueError):
        hypothetical_hit_interval(hits, shots=shots)


@pytest.mark.parametrize("invalid", [True, "0.5", float("nan"), float("inf"), -0.01, 1.01, None])
def test_invalid_or_missing_metric_probability_is_rejected(invalid: Any) -> None:
    metrics = _metrics(0.5)
    metrics["p_ground_encoding"] = invalid
    with pytest.raises(ValueError, match="p_ground_encoding"):
        precision_from_metric_probabilities(metrics)
    metrics.pop("p_ground_encoding")
    with pytest.raises(ValueError, match="p_ground_encoding"):
        precision_from_metric_probabilities(metrics)


@pytest.mark.parametrize("metric", ["p_schedule_optimal", "p_encoding_feasible"])
def test_frozen_event_containments_are_preserved(metric: str) -> None:
    metrics = _metrics(0.25)
    metrics[metric] = 0.5
    with pytest.raises(ValueError, match="containments"):
        precision_from_metric_probabilities(metrics)


def test_complete_encoding_ground_and_decoded_optimal_events_stay_distinct(
    references: dict[str, FrozenReference],
) -> None:
    vector = np.zeros(32)
    vector[m2_bits_to_basis_index("01101")] = 1
    report = _distribution_report(references["H_workload"], vector)
    events = {metric: row["reference_probability"] for metric, row in report["metrics"].items()}
    assert events == {
        "p_ground_encoding": 0,
        "p_encoding_feasible": 0,
        "p_schedule_feasible": 1,
        "p_schedule_optimal": 1,
    }
    assert report["metric_semantics_verified"]


def test_auxiliary_multiplicity_uses_complete_existing_m4_masks(
    references: dict[str, FrozenReference],
) -> None:
    reference = references["K_bounded_slack"]
    table = build_plan_table(reference)
    report = _distribution_report(reference, np.full(128, 1 / 128))
    events = report["metrics"]
    assert events["p_ground_encoding"]["reference_probability"] == 2 / 128
    assert events["p_schedule_optimal"]["reference_probability"] == 8 / 128
    for metric, mask in budget_precision.METRIC_MASKS.items():
        assert (
            events[metric]["reference_probability"]
            == np.count_nonzero(getattr(table.state_table, mask)) / 128
        )
    assert events["p_encoding_feasible"] != events["p_schedule_feasible"]


def test_existing_mc_helper_receives_both_new_n_and_prespecified_seed(
    monkeypatch: pytest.MonkeyPatch, references: dict[str, FrozenReference]
) -> None:
    calls = []

    def spy(values: Any, *, shots: int, seed: int) -> dict[str, Any]:
        calls.append((shots, seed))
        return histogram_shot_effect(values, shots=shots, seed=seed)

    monkeypatch.setattr(budget_precision, "histogram_shot_effect", spy)
    report = _distribution_report(references["A_single_job"], [0.1, 0.2, 0.3, 0.4])
    assert calls == [(512, 515151), (1024, 515151)]
    for shots in (512, 1024):
        effect = report["histogram_shot_effect"][str(shots)]
        assert effect == histogram_shot_effect([0.1, 0.2, 0.3, 0.4], shots=shots, seed=515151)
        assert effect["multinomial_replicates"] == 1000
        assert not effect["model_truth_confirmed"]
        assert not effect["hardware_randomness_controlled"]
    assert report == _distribution_report(references["A_single_job"], [0.1, 0.2, 0.3, 0.4])


def test_theoretical_blocks_are_separate_and_metadata_and_vector_are_not_mutated(
    references: dict[str, FrozenReference],
) -> None:
    vector = np.full(4, 0.25)
    before = vector.copy()
    metadata = {"path": "archive.json", "sha256": "a" * 64, "nested": {"origin": "prediction"}}
    report = precision_from_distribution(
        references["A_single_job"],
        vector,
        circuit_id="c1",
        prediction_label="ideal",
        prediction_reference=metadata,
    )
    assert report["source"] == "offline_reference"
    assert report["status"] == "OFFLINE_REFERENCE_ONLY"
    assert not report["hardware_used"]
    assert not report["measurement_results_present"]
    assert not report["m5_pass"]
    assert not report["blocks_pooled"]
    assert not report["theoretical_distribution_changes_with_shots"]
    assert not report["prediction_reference_hash_verified_by_this_function"]
    assert [row["block"] for row in report["block_references"]] == [1, 2]
    assert all(row["prediction_reference"] == metadata for row in report["block_references"])
    assert "counts" not in report
    assert "empirical" not in report
    report["block_references"][0]["prediction_reference"]["nested"]["origin"] = "changed"
    assert metadata["nested"]["origin"] == "prediction"
    assert report["prediction_reference"]["nested"]["origin"] == "prediction"
    assert report["block_references"][1]["prediction_reference"]["nested"]["origin"] == "prediction"
    np.testing.assert_array_equal(vector, before)
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize(
    "vector",
    [[0.25] * 3, [[0.25] * 4], [0.5, -0.5, 0.5, 0.5], [0.5] * 4, [float("nan"), 0, 0, 1]],
)
def test_invalid_distribution_reuses_m4_validation(
    references: dict[str, FrozenReference], vector: Any
) -> None:
    with pytest.raises(ValueError):
        _distribution_report(references["A_single_job"], vector)


def test_frozen_reference_mismatch_is_rejected(references: dict[str, FrozenReference]) -> None:
    with pytest.raises(ValueError, match="another frozen reference"):
        precision_from_distribution(
            references["A_single_job"],
            [0.25] * 4,
            circuit_id="c1",
            prediction_label="ideal",
            prediction_reference={"sha256": "a" * 64},
            table=build_plan_table(references["H_workload"]),
        )


@pytest.mark.parametrize("metadata", [{}, {"invalid": float("nan")}, {"invalid": Path("x")}])
def test_archival_metadata_must_be_present_and_json_serializable(
    references: dict[str, FrozenReference], metadata: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        precision_from_distribution(
            references["A_single_job"],
            [0.25] * 4,
            circuit_id="c1",
            prediction_label="ideal",
            prediction_reference=metadata,
        )
