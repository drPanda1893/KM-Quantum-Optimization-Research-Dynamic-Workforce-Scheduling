"""Hardware histogram analysis without the M3/M4 sampler-correctness gate.

Arbitrarily large departures from a frozen prediction are valid measurements.
Counts are matched by persistent PUB and circuit identity; repeat blocks remain
separate.  These are register histogram comparisons, never state fidelities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
from km_quantum.m5.mapping import decode_classical_counts
from km_quantum.qaoa.ordering import m2_bits_to_basis_index
from km_quantum.qaoa.references import FrozenReference

MONTE_CARLO_SEED = 314159
MONTE_CARLO_REPLICATES = 1000
CONFIDENCE_LEVEL = 0.95


def _probabilities(
    values: Sequence[float] | NDArray[np.float64], width: int
) -> NDArray[np.float64]:
    probabilities = np.asarray(values, dtype=float)
    if (
        probabilities.shape != (2**width,)
        or not np.all(np.isfinite(probabilities))
        or np.any(probabilities < 0)
        or not np.isclose(probabilities.sum(), 1, rtol=0, atol=1e-9)
    ):
        raise ValueError("Frozen probabilities must cover the normalized complete M2 state space")
    return probabilities / probabilities.sum()


def decode_register_counts(
    raw_counts: Mapping[str, int],
    *,
    shots: int,
    m2_to_classical: Sequence[int],
    classical_width: int | None = None,
) -> tuple[dict[str, int], NDArray[np.int64]]:
    """Translate actual classical bit positions into literal M2 bit strings.

    A Sampler ``m2.get_counts`` register has positions 0..n-1.  A global
    classical register fixture can supply an explicit wider classical width.
    Qiskit displays high classical positions on the left; no physical-layout
    assumption is made here.  Zero entries are legal and retained in raw data.
    """
    positions = tuple(m2_to_classical)
    width = len(positions) if classical_width is None else classical_width
    if (
        not positions
        or len(positions) > 7
        or type(width) is not int
        or width < len(positions)
        or len(set(positions)) != len(positions)
        or any(type(bit) is not int or not 0 <= bit < width for bit in positions)
    ):
        raise ValueError("Classical measurement mapping must be distinct and within the register")
    decoded = decode_classical_counts(
        raw_counts, positions, shots=shots, classical_width=classical_width
    )
    integer_counts = np.zeros(2 ** len(positions), dtype=np.int64)
    for bits, count in decoded.items():
        index = m2_bits_to_basis_index(bits)
        integer_counts[index] += count
    return decoded, integer_counts


def histogram_shot_effect(
    probabilities: Sequence[float] | NDArray[np.float64],
    *,
    shots: int,
    seed: int = MONTE_CARLO_SEED,
) -> dict[str, Any]:
    """Prespecified finite-shot TV distances from a frozen prediction.

    This descriptive range assumes independent multinomial shots from that
    prediction.  It is neither a truth test for the model nor a QPU RNG seed.
    """
    vector = np.asarray(probabilities, dtype=float)
    if len(vector) == 0 or len(vector) & (len(vector) - 1) or len(vector) > 128:
        raise ValueError("Histogram prediction must have a power-of-two dimension at most 128")
    vector = _probabilities(vector, len(vector).bit_length() - 1)
    if type(shots) is not int or shots <= 0 or type(seed) is not int or seed < 0:
        raise ValueError("Shot-effect shots and Monte-Carlo seed must be valid integers")
    draws = np.random.default_rng(seed).multinomial(shots, vector, size=MONTE_CARLO_REPLICATES)
    distances = 0.5 * np.abs(draws / shots - vector).sum(axis=1)
    quantiles = np.quantile(distances, [0.025, 0.5, 0.975])
    return {
        "distance_kind": "total_variation_of_register_histograms",
        "shots": shots,
        "monte_carlo_seed": seed,
        "multinomial_replicates": MONTE_CARLO_REPLICATES,
        "quantiles": {
            "0.025": float(quantiles[0]),
            "0.5": float(quantiles[1]),
            "0.975": float(quantiles[2]),
        },
        "assumption": "Independent multinomial shots from this fixed prediction",
        "model_truth_confirmed": False,
        "hardware_randomness_controlled": False,
    }


def analyze_counts(
    reference: FrozenReference,
    counts: Mapping[str, int],
    *,
    shots: int,
    predictions: Mapping[str, Sequence[float]],
    m2_to_classical: Sequence[int] | None = None,
    classical_width: int | None = None,
    source: str = "hardware",
    mc_seed: int = MONTE_CARLO_SEED,
    table: PlanTable | None = None,
) -> dict[str, Any]:
    """Reuse frozen M1/M2 semantics, keeping invalid plans and auxiliary bits."""
    if source not in {"hardware", "mock", "offline_simulator"}:
        raise ValueError("Counts must declare hardware, mock or offline_simulator source")
    width = reference.qubo.number_variables
    if not 1 <= width <= 7:
        raise ValueError("M5 analysis is frozen to at most seven logical variables")
    positions = tuple(range(width)) if m2_to_classical is None else tuple(m2_to_classical)
    if len(positions) != width:
        raise ValueError("Classical measurement mapping differs from M2 variable count")
    decoded, integer_counts = decode_register_counts(
        counts, shots=shots, m2_to_classical=positions, classical_width=classical_width
    )
    table = table or build_plan_table(reference)
    empirical = analyze_distribution(reference, table, integer_counts / shots)
    intervals = {}
    for name, mask_name in METRIC_MASKS.items():
        successes = int(integer_counts[getattr(table.state_table, mask_name)].sum())
        interval = binomtest(successes, shots).proportion_ci(
            confidence_level=CONFIDENCE_LEVEL, method="exact"
        )
        intervals[name] = {
            "successes": successes,
            "shots": shots,
            "frequency": successes / shots,
            "confidence_interval_95": [float(interval.low), float(interval.high)],
            "method": "Clopper-Pearson exact binomial",
            "assumption": "Independent Bernoulli shots within this circuit and block",
            "zero_hits_implies_zero_probability": False,
        }
    sampled = [plan for plan in empirical["decoded_plans"] if plan["probability"] > 0]
    best = min(
        (plan for plan in sampled if plan["schedule_feasible"]),
        key=lambda plan: (plan["m1_objective"], plan["plan_key"]),
        default=None,
    )
    comparisons = {}
    for name, values in sorted(predictions.items()):
        frozen = _probabilities(values, width)
        predicted = analyze_distribution(reference, table, frozen, include_plan_rows=False)
        differences = {
            metric: {
                "signed_percentage_points": 100 * (empirical[metric] - predicted[metric]),
                "absolute_percentage_points": 100 * abs(empirical[metric] - predicted[metric]),
            }
            for metric in METRIC_MASKS
        }
        comparisons[name] = {
            "prediction_metrics": predicted,
            "metric_differences": differences,
            "total_variation_distance": float(0.5 * np.abs(integer_counts / shots - frozen).sum()),
            "distance_kind": "register_histogram_distance; not quantum state fidelity",
            "finite_shot_effect": histogram_shot_effect(frozen, shots=shots, seed=mc_seed),
        }
    return {
        "artifact_type": "m5_hardware_count_analysis"
        if source == "hardware"
        else "m5_test_count_analysis",
        "source": source,
        "hardware_used": source == "hardware",
        "analysis_status": "ANALYZED" if source == "hardware" else "TEST_DATA_ONLY",
        "m5_pass": False,
        "shots": shots,
        "raw_counts": dict(sorted(counts.items())),
        "m2_decoded_counts": decoded,
        "m2_to_classical": list(positions),
        "empirical": empirical,
        "empirical_mean_qubo_energy_with_offset": empirical["expected_qubo_energy"],
        "event_intervals": intervals,
        "distinct_sampled_bitstrings": int(np.count_nonzero(integer_counts)),
        "distinct_sampled_decoded_plans": len(sampled),
        "best_valid_sample_plan": None if best is None else best["schedule"],
        "best_valid_sample_objective": None if best is None else best["m1_objective"],
        "best_valid_sample_objective_gap": (
            None if best is None else best["m1_objective"] - table.state_table.classical_objective
        ),
        "comparisons": comparisons,
        "hardware_prediction_agreement_is_acceptance_gate": False,
        "postselection_applied": False,
        "error_mitigation_applied": False,
        "decoder_repair_applied": False,
        "limitations": [
            "Known four-case diagnostic suite; no new Warm-Start generalization "
            "or superiority claim",
            "Two repeat blocks do not establish long-term stability or independent planning cases",
            "Intervals assume independent shots; correlations and drift are not identified",
            "Histogram comparisons are not tomography or state fidelity",
        ],
    }


def analyze_results(
    expected_pubs: Sequence[Mapping[str, Any]],
    raw_results: Sequence[Mapping[str, Any]],
    predictions_by_circuit: Mapping[str, Mapping[str, Sequence[float]]],
    references: Mapping[str, FrozenReference],
    *,
    mc_seed: int = MONTE_CARLO_SEED,
) -> dict[str, Any]:
    """Match complete/partial real or mock records by persistent IDs.

    Expected rows carry ``pub_id,circuit_id,circuit_sha256,case_name,block,shots``.
    Raw rows carry matching IDs/hash, ``counts,shots,source,hardware_used``.
    Missing rows are reported without invented data or a hardware PASS.
    """
    expected: dict[str, Mapping[str, Any]] = {}
    for pub in expected_pubs:
        pub_id = pub["pub_id"]
        if not isinstance(pub_id, str) or not pub_id or pub_id in expected:
            raise ValueError("Expected PUB IDs must be nonempty and unique")
        expected[pub_id] = pub
    if not raw_results:
        return {
            "schema_version": 1,
            "artifact_type": "m5_count_analysis_collection",
            "analysis_status": "NOT_AVAILABLE",
            "hardware_used": False,
            "m5_pass": False,
            "expected_pub_count": len(expected),
            "analyzed_pub_count": 0,
            "missing_pub_ids": sorted(expected),
            "blocks": {},
            "observed_hardware_agreement": "NOT_AVAILABLE",
        }
    observed: set[str] = set()
    blocks: dict[str, list[dict[str, Any]]] = {}
    hardware_rows = 0
    tables: dict[str, PlanTable] = {}
    for raw in raw_results:
        pub_id = raw.get("pub_id")
        if pub_id not in expected or pub_id in observed:
            raise ValueError("Unknown or duplicate persistent PUB ID")
        pub = expected[pub_id]
        for field in ("circuit_id", "circuit_sha256", "shots"):
            if raw.get(field) != pub[field]:
                raise ValueError(f"PUB {pub_id} has mismatched {field}")
        source = raw.get("source")
        if not isinstance(source, str):
            raise ValueError("Result source must be an explicit string")
        if source == "hardware" and raw.get("hardware_used") is not True:
            raise ValueError("Hardware source requires explicit real hardware provenance")
        if source != "hardware" and raw.get("hardware_used") is not False:
            raise ValueError("Test/simulator counts cannot claim hardware provenance")
        observed.add(pub_id)
        case = pub["case_name"]
        reference = references[case]
        if case not in tables:
            tables[case] = build_plan_table(reference)
        report = analyze_counts(
            reference,
            raw["counts"],
            shots=pub["shots"],
            predictions=predictions_by_circuit[pub["circuit_id"]],
            m2_to_classical=pub.get("m2_to_classical"),
            classical_width=pub.get("classical_width"),
            source=source,
            mc_seed=mc_seed,
            table=tables[case],
        )
        report.update(
            {
                "pub_id": pub_id,
                "circuit_id": pub["circuit_id"],
                "case_name": case,
                "block": pub["block"],
            }
        )
        blocks.setdefault(str(pub["block"]), []).append(report)
        hardware_rows += int(source == "hardware")
    missing = sorted(set(expected) - observed)
    all_real = hardware_rows == len(raw_results)
    status = "PARTIAL" if missing else "ANALYZED"
    if not all_real:
        status = "TEST_DATA_ONLY"
    return {
        "schema_version": 1,
        "artifact_type": "m5_count_analysis_collection",
        "analysis_status": status,
        "hardware_used": hardware_rows > 0,
        "m5_pass": False,
        "expected_pub_count": len(expected),
        "analyzed_pub_count": len(observed),
        "real_hardware_pub_count": hardware_rows,
        "missing_pub_ids": missing,
        "blocks": {
            block: sorted(rows, key=lambda row: row["pub_id"])
            for block, rows in sorted(blocks.items())
        },
        "blocks_pooled": False,
        "observed_hardware_agreement": "DESCRIPTIVE_ONLY" if hardware_rows else "NOT_AVAILABLE",
        "prediction_agreement_is_acceptance_gate": False,
    }
