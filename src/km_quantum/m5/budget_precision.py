"""Offline precision comparison for the unchanged M4 prediction distributions.

These calculations describe one circuit in one repeat block at 512 or 1024
shots. They contain no observed counts and create no hardware result, execution
plan, approval or account connection. Both blocks use the same theoretical
reference; their future measurements must remain separate.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from numbers import Real
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.stats import binomtest

from km_quantum.m4.metrics import (
    METRIC_MASKS,
    PlanTable,
    analyze_distribution,
    build_plan_table,
)
from km_quantum.m5.analysis import histogram_shot_effect
from km_quantum.qaoa.references import FrozenReference

COMPARISON_SHOTS = (512, 1024)
MONTE_CARLO_SEED = 515151
CONFIDENCE_LEVEL = 0.95
HYPOTHETICAL_HITS = (0, 1, 2)

METRIC_MEANINGS = {
    "p_ground_encoding": "Ground state of the complete encoding, including auxiliary bits",
    "p_schedule_optimal": "Optimal decoded M1 schedule, regardless of auxiliary encoding",
    "p_encoding_feasible": "Feasible complete encoding",
    "p_schedule_feasible": "Feasible decoded M1 schedule",
}


def hypothetical_hit_interval(successes: int, *, shots: int) -> dict[str, Any]:
    """Exact two-sided 95% intervals for a declared hypothetical hit count.

    In particular, a zero-hit example has a positive upper bound. It is not an
    observed result, and its endpoints do not depend on the reference model's p.
    """
    if (
        type(shots) is not int
        or shots not in COMPARISON_SHOTS
        or type(successes) is not int
        or not 0 <= successes <= shots
    ):
        raise ValueError("Hypothetical hits must be integral and within 512 or 1024 shots")
    interval = binomtest(successes, shots).proportion_ci(
        confidence_level=CONFIDENCE_LEVEL, method="exact"
    )
    return {
        "outcome_kind": "HYPOTHETICAL",
        "observed": False,
        "hypothetical_successes": successes,
        "shots": shots,
        "hypothetical_frequency": successes / shots,
        "confidence_level": CONFIDENCE_LEVEL,
        "confidence_interval_95": [float(interval.low), float(interval.high)],
        "method": "Clopper-Pearson exact two-sided binomial",
        "assumption": "Independent Bernoulli shots within one circuit and one block",
        "zero_hits_implies_zero_probability": False,
    }


def _event_precision(probability: float, shots: int) -> dict[str, Any]:
    variance = probability * (1 - probability) / shots
    standard_error = math.sqrt(variance)
    zero_probability = 0.0 if probability == 1 else math.exp(shots * math.log1p(-probability))
    return {
        "shots": shots,
        "variance": variance,
        "standard_error": standard_error,
        "standard_error_percentage_points": 100 * standard_error,
        "expected_hits_if_reference_is_true": shots * probability,
        "probability_of_zero_hits_if_reference_is_true": zero_probability,
    }


def precision_from_metric_probabilities(
    probabilities_by_metric: Mapping[str, float],
) -> dict[str, Any]:
    """Compare Bernoulli precision without generating or consuming counts.

    The four names and event meanings are the frozen M4 definitions. Extra
    fields from an M4 distribution analysis are ignored. Ratios are undefined
    when both reference variances are zero; no 0/0 value is serialized.
    """
    probabilities: dict[str, float] = {}
    for metric in METRIC_MASKS:
        value = probabilities_by_metric.get(metric)
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError(f"A finite probability in [0,1] is required for {metric}")
        probabilities[metric] = float(value)
    if not (
        probabilities["p_ground_encoding"] <= probabilities["p_schedule_optimal"] + 1e-12
        and probabilities["p_schedule_optimal"] <= probabilities["p_schedule_feasible"] + 1e-12
        and probabilities["p_encoding_feasible"] <= probabilities["p_schedule_feasible"] + 1e-12
    ):
        raise ValueError("Probabilities violate the frozen M4 event containments")
    metrics = {}
    for metric, probability in probabilities.items():
        nonzero_variance = 0 < probability < 1
        metrics[metric] = {
            "meaning": METRIC_MEANINGS[metric],
            "reference_probability": probability,
            "by_shots": {
                str(shots): _event_precision(probability, shots) for shots in COMPARISON_SHOTS
            },
            "standard_error_ratio_512_over_1024": math.sqrt(2) if nonzero_variance else None,
            "variance_ratio_512_over_1024": 2.0 if nonzero_variance else None,
            "ratio_defined": nonzero_variance,
        }
    return {
        "schema_version": 1,
        "artifact_type": "m5_offline_sampling_precision",
        "status": "OFFLINE_REFERENCE_ONLY",
        "source": "offline_reference",
        "hardware_used": False,
        "measurement_results_present": False,
        "m5_pass": False,
        "comparison_shots_per_circuit_per_block": list(COMPARISON_SHOTS),
        "precision_scope": "one_circuit_one_block",
        "blocks_pooled": False,
        "theoretical_distribution_changes_with_shots": False,
        "metrics": metrics,
        "worst_case_bernoulli_precision": {
            "reference_probability": 0.5,
            "by_shots": {str(shots): _event_precision(0.5, shots) for shots in COMPARISON_SHOTS},
        },
        "hypothetical_hit_intervals": {
            str(shots): {
                str(hits): hypothetical_hit_interval(hits, shots=shots)
                for hits in HYPOTHETICAL_HITS
            }
            for shots in COMPARISON_SHOTS
        },
        "assumptions": [
            "The reference probability is held fixed for both shot counts",
            "Bernoulli precision assumes independent shots within one circuit and one block",
            "Reference simulations are not observed hardware measurements",
            "Repeat blocks remain separate; correlations and drift are not established",
        ],
    }


def precision_from_distribution(
    reference: FrozenReference,
    probabilities: Sequence[float] | NDArray[np.float64],
    *,
    circuit_id: str,
    prediction_label: str,
    prediction_reference: Mapping[str, Any],
    table: PlanTable | None = None,
) -> dict[str, Any]:
    """Use the existing M4 event masks and M5 finite-shot histogram helper.

    The caller supplies archival hash/path metadata after validating the source
    artifact. This function copies that metadata without claiming to verify a
    filesystem hash. The vector is analyzed locally; no circuit is regenerated.
    The Monte-Carlo seed is fixed by this comparison, not selected by a caller.
    """
    if (
        not isinstance(circuit_id, str)
        or not circuit_id
        or not isinstance(prediction_label, str)
        or not prediction_label
        or not prediction_reference
    ):
        raise ValueError("Circuit, prediction label and archival reference metadata are required")
    metadata = deepcopy(dict(prediction_reference))
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Prediction reference metadata must be finite JSON data") from exc
    if not 1 <= reference.qubo.number_variables <= 7:
        raise ValueError("The frozen M5 comparison requires one to seven logical variables")
    vector = np.asarray(probabilities, dtype=np.float64)
    table = table or build_plan_table(reference)
    exact = analyze_distribution(reference, table, vector, include_plan_rows=False)
    result = precision_from_metric_probabilities({metric: exact[metric] for metric in METRIC_MASKS})
    result.update(
        circuit_id=circuit_id,
        case_name=reference.name,
        frozen_reference_hash=reference.reference_hash,
        prediction_label=prediction_label,
        prediction_reference=metadata,
        prediction_reference_hash_verified_by_this_function=False,
        metric_semantics_verified=exact["metric_semantics_verified"],
        histogram_shot_effect={
            str(shots): histogram_shot_effect(vector, shots=shots, seed=MONTE_CARLO_SEED)
            for shots in COMPARISON_SHOTS
        },
        block_references=[
            {
                "block": block,
                "circuit_id": circuit_id,
                "prediction_label": prediction_label,
                "prediction_reference": deepcopy(metadata),
                "scope": "Same theoretical reference; analyze future counts separately",
                "measurement_results_present": False,
            }
            for block in (1, 2)
        ],
    )
    return result
