"""Application metrics with literal decoding and explicit auxiliary aggregation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.stats import binomtest

from km_quantum.qaoa.analysis import StateTable, analysis_for_probabilities, build_state_table
from km_quantum.qaoa.ordering import m2_bits_to_basis_index, qiskit_bitstring_to_m2_bits
from km_quantum.qaoa.references import FrozenReference, load_references
from km_quantum.qubo.decoder import auxiliary_values, decode_bitstring
from km_quantum.qubo.models import evaluate_qubo_energy

SAMPLING_TAIL_ALPHA = 1e-9
METRIC_MASKS = {
    "p_ground_encoding": "ground",
    "p_encoding_feasible": "encoded_valid",
    "p_schedule_feasible": "valid",
    "p_schedule_optimal": "decoded_optimal",
}
M3_METRIC_REMAP = {
    "p_ground_encoding": "ground_state_probability",
    "p_encoding_feasible": "encoded_valid_probability",
    "p_schedule_feasible": "valid_probability",
    "p_schedule_optimal": "decoded_optimal_schedule_probability",
}


@dataclass(frozen=True)
class PlanTable:
    """A checked complete state table and its many-to-one literal plan projection."""

    state_table: StateTable
    state_to_plan: NDArray[np.int64]
    plans: tuple[dict[str, Any], ...]
    reference_hash: str


def _plan_key(schedule: Any) -> str:
    return json.dumps(
        {
            "assignments": sorted((item.job_id, item.worker_id) for item in schedule.assignments),
            "unassigned_job_ids": sorted(schedule.unassigned_job_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def build_plan_table(reference: FrozenReference, max_qubits: int = 10) -> PlanTable:
    state = build_state_table(reference, max_qubits=max_qubits)
    if np.any(state.ground & ~state.decoded_optimal):
        raise ValueError("ground encodings must decode to proven optimal schedules")
    if np.any(state.decoded_optimal & ~state.valid):
        raise ValueError("optimal schedules must be valid")
    if np.any(state.encoded_valid & ~state.valid):
        raise ValueError("encoding feasibility must imply decoded schedule feasibility")
    groups: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    primary_keys: dict[str, str] = {}
    # Decoder evaluation is independent of energy/ground membership. Every
    # primary plan remains visible, including invalid and nonoptimal plans.
    for index, bits in enumerate(state.bits):
        primary = bits[: reference.qubo.mapping.primary_variable_count]
        if primary not in primary_keys:
            schedule = decode_bitstring(reference.instance, reference.qubo.mapping, bits)
            primary_keys[primary] = _plan_key(schedule)
        key = primary_keys[primary]
        keys.append(key)
        if key not in groups:
            groups[key] = {
                "plan_key": key,
                "schedule": decode_bitstring(
                    reference.instance, reference.qubo.mapping, bits
                ).model_dump(mode="json"),
                "schedule_feasible": bool(state.valid[index]),
                "schedule_optimal": bool(state.decoded_optimal[index]),
                "m1_objective": state.objectives[index],
                "encoding_multiplicity": 0,
                "feasible_encoding_multiplicity": 0,
                "ground_encoding_multiplicity": 0,
            }
        group = groups[key]
        if (group["schedule_feasible"], group["m1_objective"]) != (
            bool(state.valid[index]),
            state.objectives[index],
        ):
            raise ValueError("literal plan identity has inconsistent objective/validity")
        group["encoding_multiplicity"] += 1
        group["feasible_encoding_multiplicity"] += int(state.encoded_valid[index])
        group["ground_encoding_multiplicity"] += int(state.ground[index])
    ordered = sorted(groups)
    positions = {key: index for index, key in enumerate(ordered)}
    state_to_plan = np.asarray([positions[key] for key in keys], dtype=np.int64)
    state_to_plan.flags.writeable = False
    return PlanTable(
        state_table=state,
        state_to_plan=state_to_plan,
        plans=tuple(groups[key] for key in ordered),
        reference_hash=reference.reference_hash,
    )


def analyze_distribution(
    reference: FrozenReference,
    table: PlanTable,
    probabilities: NDArray[np.float64] | list[float],
    *,
    include_plan_rows: bool = True,
) -> dict[str, Any]:
    """Analyze one declared ideal, before-readout, after-readout or sampled layer."""
    if table.reference_hash != reference.reference_hash:
        raise ValueError("plan table belongs to another frozen reference")
    values = np.asarray(probabilities, dtype=np.float64)
    if values.shape != table.state_table.energies.shape:
        raise ValueError("probabilities must cover the complete state space")
    expectation = float(np.dot(values, table.state_table.energies))
    legacy = analysis_for_probabilities(reference, table.state_table, values, expectation)
    canonical = {name: float(legacy[original]) for name, original in M3_METRIC_REMAP.items()}
    if not (
        canonical["p_ground_encoding"] <= canonical["p_schedule_optimal"] + 1e-12
        and canonical["p_schedule_optimal"] <= canonical["p_schedule_feasible"] + 1e-12
        and canonical["p_encoding_feasible"] <= canonical["p_schedule_feasible"] + 1e-12
    ):
        raise ValueError("probability containment failed")
    masses = np.bincount(table.state_to_plan, weights=values, minlength=len(table.plans))
    if not np.isclose(float(masses.sum()), 1.0, rtol=0, atol=1e-10):
        raise ValueError("aggregated plan probability is not normalized")
    schedule_optimal_mass = sum(
        float(mass)
        for plan, mass in zip(table.plans, masses, strict=True)
        if plan["schedule_optimal"]
    )
    if not np.isclose(schedule_optimal_mass, canonical["p_schedule_optimal"], atol=1e-12):
        raise ValueError("plan aggregation changes optimal schedule mass")
    result: dict[str, Any] = {
        **canonical,
        "expected_qubo_energy": expectation,
        "ground_energy": reference.ground_energy,
        "maximum_qubo_energy": table.state_table.max_energy,
        "energy_residual": legacy["energy_residual"],
        "normalized_energy_error": legacy["normalized_energy_error"],
        "conditional_valid_m1_objective": legacy["expected_valid_objective"],
        "conditional_objective_feasibility_rate": canonical["p_schedule_feasible"],
        "distinct_decoded_plans_with_positive_probability": int(np.count_nonzero(masses > 0)),
        "possible_decoded_plan_count": len(table.plans),
        "auxiliary_bit_count": (
            reference.qubo.number_variables - reference.qubo.mapping.primary_variable_count
        ),
        "ground_encoding_count": len(reference.ground_bits),
        "ground_decoded_plan_count": sum(
            plan["ground_encoding_multiplicity"] > 0 for plan in table.plans
        ),
        "ground_auxiliary_degeneracy": [
            {
                "plan_key": plan["plan_key"],
                "ground_encoding_multiplicity": plan["ground_encoding_multiplicity"],
            }
            for plan in table.plans
            if plan["ground_encoding_multiplicity"] > 0
        ],
        "maximum_encoding_multiplicity_per_plan": max(
            (plan["encoding_multiplicity"] for plan in table.plans), default=0
        ),
        "metric_semantics_verified": True,
    }
    if include_plan_rows:
        result["decoded_plans"] = [
            {**plan, "probability": float(mass)}
            for plan, mass in zip(table.plans, masses, strict=True)
        ]
    return result


def sample_metrics(
    reference: FrozenReference,
    table: PlanTable,
    counts: dict[str, int],
    *,
    shots: int,
    seed: int,
    probabilities: NDArray[np.float64] | list[float],
) -> dict[str, Any]:
    """Keep every sample; confidence intervals concern finite sampling only."""
    if type(shots) is not int or shots < 1 or type(seed) is not int or seed < 0:
        raise ValueError("shots must be positive and seed a nonnegative integer")
    if not counts or any(type(value) is not int or value <= 0 for value in counts.values()):
        raise ValueError("sample counts must be positive integers")
    if sum(counts.values()) != shots:
        raise ValueError("sample counts do not sum to declared shots")
    exact = analyze_distribution(reference, table, probabilities, include_plan_rows=False)
    frequencies = np.zeros(len(table.state_table.bits), dtype=np.float64)
    integer_counts = np.zeros(len(frequencies), dtype=np.int64)
    for displayed, count in sorted(counts.items()):
        index = m2_bits_to_basis_index(
            qiskit_bitstring_to_m2_bits(displayed, reference.qubo.number_variables)
        )
        integer_counts[index] += count
        frequencies[index] += count / shots
    empirical = analyze_distribution(reference, table, frequencies)
    intervals: dict[str, Any] = {}
    for name, mask_name in METRIC_MASKS.items():
        mask = getattr(table.state_table, mask_name)
        successes = int(integer_counts[mask].sum())
        probability = float(np.clip(exact[name], 0, 1))
        interval = binomtest(successes, shots).proportion_ci(confidence_level=0.95)
        test = binomtest(successes, shots, probability)
        intervals[name] = {
            "count": successes,
            "frequency": successes / shots,
            "exact_probability": probability,
            "confidence_interval_95": [float(interval.low), float(interval.high)],
            "absolute_sampling_deviation": abs(successes / shots - probability),
            "standard_error": float(np.sqrt(probability * (1 - probability) / shots)),
            "binomial_two_sided_pvalue": float(test.pvalue),
            "sampling_verified": bool(test.pvalue >= SAMPLING_TAIL_ALPHA),
        }
    sampled_plans = [plan for plan in empirical["decoded_plans"] if plan["probability"] > 0]
    valid_plans = [plan for plan in sampled_plans if plan["schedule_feasible"]]
    best = min(valid_plans, key=lambda plan: (plan["m1_objective"], plan["plan_key"]), default=None)
    return {
        "shots": shots,
        "sampling_seed": seed,
        "counts": dict(sorted(counts.items())),
        "distinct_sampled_bitstrings": len(counts),
        "distinct_sampled_decoded_plans": len(sampled_plans),
        "empirical": empirical,
        "sampling_intervals": intervals,
        "sampling_tail_alpha": SAMPLING_TAIL_ALPHA,
        "sampling_verified": all(row["sampling_verified"] for row in intervals.values()),
        "best_valid_sample_plan": None if best is None else best["schedule"],
        "best_valid_sample_objective": None if best is None else best["m1_objective"],
        "best_valid_sample_objective_gap": (
            None if best is None else best["m1_objective"] - table.state_table.classical_objective
        ),
        "valid_sample_frequency": intervals["p_schedule_feasible"]["frequency"],
    }


def _counterexample(reference: FrozenReference, bits: str) -> dict[str, Any]:
    table = build_plan_table(reference, max_qubits=15)
    index = table.state_table.bits.index(bits)
    distribution = np.zeros(len(table.state_table.bits))
    distribution[index] = 1
    report = analyze_distribution(reference, table, distribution)
    return {
        "reference_id": reference.name,
        "m2_bits": bits,
        "reference_hash": reference.reference_hash,
        "energy_breakdown": evaluate_qubo_energy(reference.qubo, bits).model_dump(),
        "auxiliary_values": auxiliary_values(reference.qubo.mapping, bits),
        "ground_energy": reference.ground_energy,
        "decoded_m1_objective": table.state_table.objectives[index],
        "metrics_for_point_mass": report,
    }


def create_metric_semantics_audit(root: Path) -> dict[str, Any]:
    """Versioned M4 aliases; this neither changes nor rejudges historical M3 PASS."""
    references = load_references(root)
    checked_states = 0
    nonground_decoded_optimal_states = 0
    zero_penalty_inflated_business_states = 0
    for reference in references.values():
        table = build_plan_table(reference, max_qubits=15)
        checked_states += len(table.state_table.bits)
        nonground_decoded_optimal_states += int(
            np.count_nonzero(table.state_table.decoded_optimal & ~table.state_table.ground)
        )
        for index in np.flatnonzero(table.state_table.encoded_valid):
            breakdown = evaluate_qubo_energy(reference.qubo, table.state_table.bits[index])
            zero_penalty_inflated_business_states += (
                breakdown.business != table.state_table.objectives[index]
            )
    rows = []
    for path in sorted((root / "results" / "m3" / "runs").glob("*.json")):
        old = json.loads(path.read_text(encoding="utf-8"))
        canonical = {name: old[original] for name, original in M3_METRIC_REMAP.items()}
        g, e, v, o = (canonical[name] for name in METRIC_MASKS)
        if not (g <= o + 1e-12 and o <= v + 1e-12 and e <= v + 1e-12):
            raise ValueError(f"M3 saved metric containment fails: {old['run_id']}")
        if abs(old["optimal_valid_probability"] - g) > 1e-12:
            raise ValueError("M3 energy-consistent optimum differs from ground")
        rows.append({"run_id": old["run_id"], "reference_id": old["reference_name"], **canonical})
    if len(rows) != 660:
        raise ValueError("semantic audit requires all 660 historical M3 runs")
    return {
        "schema_version": 1,
        "artifact_type": "m4_m3_metric_semantics_audit",
        "status": "PASS",
        "historical_m3_files_modified": False,
        "historical_m3_correction_required": False,
        "historical_m3_algorithm_or_mapping_error": False,
        "impact": "Explicit application-oriented aliases and decoded-plan aggregation only.",
        "metric_remap": M3_METRIC_REMAP,
        "m3_optimal_valid_probability_meaning": (
            "zero-penalty optimal schedule encoding with business/M1 objective equality"
        ),
        "checked_frozen_references": len(references),
        "checked_basis_states": checked_states,
        "nonground_states_decoding_optimal_schedule": nonground_decoded_optimal_states,
        "zero_hard_penalty_states_with_inflated_encoded_business": (
            zero_penalty_inflated_business_states
        ),
        "m3_saved_rows": len(rows),
        "m3_rows_decoded_optimal_exceeds_ground": sum(
            row["p_schedule_optimal"] > row["p_ground_encoding"] + 1e-12 for row in rows
        ),
        "counterexamples": {
            "wrong_workload_auxiliary": _counterexample(references["H_workload"], "01101"),
            "zero_penalty_inflated_imbalance": _counterexample(
                references["M_active_imbalance"], "0011011010"
            ),
            "zero_penalty_optimal_schedule_nonground": _counterexample(
                references["O_capacity_and_imbalance"], "011000001010101"
            ),
        },
        "rows": rows,
    }
