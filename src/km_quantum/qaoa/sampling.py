"""Finite, seeded measurements from the final ideal state, after exact training."""

from __future__ import annotations

from typing import Any

import numpy as np
from qiskit import QuantumCircuit
from qiskit.primitives import StatevectorSampler
from qiskit.quantum_info import Statevector
from scipy.stats import binomtest

from km_quantum.qaoa.analysis import StateTable
from km_quantum.qaoa.operators import OPERATOR_ATOL, OPERATOR_RTOL
from km_quantum.qaoa.ordering import m2_bits_to_basis_index, qiskit_bitstring_to_m2_bits
from km_quantum.qaoa.references import FrozenReference

# Frozen before evaluation: <= 6.6e-7 union-bound rejection risk over 660 runs.
SAMPLING_TAIL_ALPHA = 1e-9


def analyze_counts(
    reference: FrozenReference,
    table: StateTable,
    counts: dict[str, int],
    *,
    shots: int,
    seed: int,
    exact_ground_probability: float,
) -> dict[str, Any]:
    if type(shots) is not int or shots < 1 or type(seed) is not int or seed < 0:
        raise ValueError("shots must be positive and seed nonnegative integers")
    if table.reference_hash != reference.reference_hash or not table.checked_ground_set:
        raise ValueError("Sampling needs the checked state table for this frozen reference")
    if (
        not np.isfinite(exact_ground_probability)
        or not -1e-12 <= exact_ground_probability <= 1 + 1e-12
    ):
        raise ValueError("exact ground probability must lie in [0,1]")
    if not counts or any(type(count) is not int or count < 1 for count in counts.values()):
        raise ValueError("Every reported sample count must be a positive integer")
    if sum(counts.values()) != shots:
        raise ValueError("Counts must sum to the declared shot count")
    for displayed in counts:
        qiskit_bitstring_to_m2_bits(displayed, reference.qubo.number_variables)
    rows: list[dict[str, Any]] = []
    ground_count = valid_count = encoded_count = optimal_count = decoded_optimal_count = 0
    sampled_objectives: list[int] = []
    for displayed, count in sorted(counts.items()):
        bits = qiskit_bitstring_to_m2_bits(displayed, reference.qubo.number_variables)
        index = m2_bits_to_basis_index(bits)
        ground_count += count * bool(table.ground[index])
        valid_count += count * bool(table.valid[index])
        encoded_count += count * bool(table.encoded_valid[index])
        optimal_count += count * bool(table.optimal_valid[index])
        decoded_optimal_count += count * bool(table.decoded_optimal[index])
        objective = table.objectives[index]
        if objective is not None:
            sampled_objectives.append(objective)
        rows.append(
            {
                "qiskit_bitstring": displayed,
                "m2_bits": table.bits[index],
                "count": count,
                "empirical_probability": count / shots,
                "energy": float(table.energies[index]),
                "ground": bool(table.ground[index]),
                "valid": bool(table.valid[index]),
                "encoded_valid": bool(table.encoded_valid[index]),
                "optimal_valid": bool(table.optimal_valid[index]),
                "decoded_optimal": bool(table.decoded_optimal[index]),
                "m1_objective": objective,
            }
        )
    modal = min(counts, key=lambda key: (-counts[key], key))
    if optimal_count != ground_count:
        raise ValueError("Sampled ground and energy-consistent optimum counts differ")
    probability = float(np.clip(exact_ground_probability, 0, 1))
    test = binomtest(ground_count, shots, probability)
    interval = binomtest(ground_count, shots).proportion_ci(confidence_level=0.95)
    return {
        "shots": shots,
        "sampler_seed": seed,
        "unique_bitstrings": len(counts),
        "counts": counts,
        "state_samples": rows,
        "best_sampled_energy": min(row["energy"] for row in rows),
        "modal_bitstring": modal,
        "modal_m2_bits": "".join(map(str, qiskit_bitstring_to_m2_bits(modal))),
        "modal_probability": counts[modal] / shots,
        "ground_state_observed": ground_count > 0,
        "optimum_observed": ground_count > 0,
        "ground_state_samples": ground_count,
        "optimal_valid_sample_count": optimal_count,
        "decoded_optimal_schedule_sample_count": decoded_optimal_count,
        "decoded_optimal_schedule_observed": decoded_optimal_count > 0,
        "empirical_decoded_optimal_schedule_frequency": decoded_optimal_count / shots,
        "empirical_ground_frequency": ground_count / shots,
        "exact_ground_probability": exact_ground_probability,
        "valid_sample_count": valid_count,
        "invalid_sample_count": shots - valid_count,
        "encoded_valid_sample_count": encoded_count,
        "empirical_feasibility_rate": valid_count / shots,
        "best_decoded_m1_objective": min(sampled_objectives) if sampled_objectives else None,
        "ground_frequency_absolute_difference": abs(ground_count / shots - probability),
        "ground_frequency_standard_error": float(np.sqrt(probability * (1 - probability) / shots)),
        "ground_frequency_confidence_interval_95": [float(interval.low), float(interval.high)],
        "binomial_two_sided_pvalue": float(test.pvalue),
        "sampling_tail_alpha": SAMPLING_TAIL_ALPHA,
        "sampling_verified": bool(test.pvalue >= SAMPLING_TAIL_ALPHA),
        "hardware_used": False,
        "noise_used": False,
    }


def sample_state(
    reference: FrozenReference,
    bound_circuit: QuantumCircuit,
    table: StateTable,
    *,
    shots: int = 1024,
    seed: int = 0,
    exact_ground_probability: float,
) -> dict[str, Any]:
    if bound_circuit.parameters or bound_circuit.num_qubits != reference.qubo.number_variables:
        raise ValueError("Sampler requires the matching fully bound unmeasured circuit")
    if any(item.operation.name == "measure" for item in bound_circuit.data):
        raise ValueError("Pass the unmeasured final circuit; sampling adds only final measurements")
    if type(shots) is not int or shots < 1 or type(seed) is not int or seed < 0:
        raise ValueError("shots must be positive and seed nonnegative integers")
    probabilities = Statevector.from_instruction(bound_circuit).probabilities()
    actual_ground = float(probabilities[table.ground].sum())
    if not np.isclose(
        actual_ground, exact_ground_probability, atol=OPERATOR_ATOL, rtol=OPERATOR_RTOL
    ):
        raise ValueError("Declared ground probability differs from the supplied final circuit")
    if bound_circuit.num_qubits:
        measured = bound_circuit.measure_all(inplace=False)
        result = StatevectorSampler(seed=seed).run([measured], shots=shots).result()[0]
        counts = result.data.meas.get_counts()
    else:
        counts = {"": shots}
    return analyze_counts(
        reference,
        table,
        counts,
        shots=shots,
        seed=seed,
        exact_ground_probability=exact_ground_probability,
    )
