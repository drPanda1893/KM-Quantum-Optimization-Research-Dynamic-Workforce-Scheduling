"""Exhaustive state analysis using the unchanged M1 decoder, validator and objective."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np
from numpy.typing import NDArray

from km_quantum.metrics import evaluate_objective, validate_schedule
from km_quantum.qubo.decoder import decode_bitstring
from km_quantum.qubo.ising import bits_to_spins
from km_quantum.qubo.models import evaluate_qubo_energy

from .operators import OPERATOR_ATOL, OPERATOR_RTOL
from .ordering import basis_index_to_m2_bits
from .references import FrozenReference, validate_reference


@dataclass(frozen=True)
class StateTable:
    energies: NDArray[np.float64]
    valid: NDArray[np.bool_]
    encoded_valid: NDArray[np.bool_]
    optimal_valid: NDArray[np.bool_]
    decoded_optimal: NDArray[np.bool_]
    ground: NDArray[np.bool_]
    bits: tuple[str, ...]
    objectives: tuple[int | None, ...]
    max_energy: int
    classical_objective: int
    checked_ground_set: bool
    reference_hash: str


def build_state_table(reference: FrozenReference, max_qubits: int = 14) -> StateTable:
    """Check every state and cache each independent primary-schedule analysis.

    Decoded-optimum schedules can have bad slack or inflated imbalance bits.
    ``optimal_valid`` therefore also requires zero penalties and pointwise
    agreement between the encoded business energy and the M1 objective.
    """
    if type(max_qubits) is not int or max_qubits < 0:
        raise ValueError("max_qubits must be a nonnegative integer")
    validate_reference(reference)
    model = reference.qubo
    n = model.number_variables
    if n > max_qubits:
        raise ValueError(f"{reference.name}: {n} qubits exceed max_qubits={max_qubits}")
    count = 1 << n
    energies = np.empty(count, dtype=np.float64)
    valid = np.zeros(count, dtype=np.bool_)
    encoded_valid = np.zeros(count, dtype=np.bool_)
    optimal_valid = np.zeros(count, dtype=np.bool_)
    decoded_optimal = np.zeros(count, dtype=np.bool_)
    ground = np.zeros(count, dtype=np.bool_)
    serialized: list[str] = []
    objectives: list[int | None] = []
    primary_count = model.mapping.primary_variable_count
    cache: dict[tuple[int, ...], tuple[bool, int | None]] = {}
    exact_energies: list[int] = []
    for index in range(count):
        bits = basis_index_to_m2_bits(index, n)
        bitstring = "".join(map(str, bits))
        serialized.append(bitstring)
        energy = model.energy(bits)
        if reference.ising.energy4(bits_to_spins(bits)) != 4 * energy:
            raise ValueError(f"{reference.name}: QUBO/Ising energy differs at {bitstring}")
        # The ideal simulator uses double precision. Reject integer energies
        # outside its exact range rather than silently collapsing energy levels.
        if abs(energy) > (1 << 53) - 1:
            raise ValueError("QUBO energies exceed the exact integer range of float64")
        exact_energies.append(energy)
        energies[index] = energy
        primary = bits[:primary_count]
        if primary not in cache:
            schedule = decode_bitstring(reference.instance, model.mapping, bits)
            is_valid = validate_schedule(reference.instance, schedule).is_valid
            objective = (
                evaluate_objective(reference.instance, schedule, model.config.objective).total
                if is_valid
                else None
            )
            cache[primary] = is_valid, objective
        is_valid, objective = cache[primary]
        objectives.append(objective)
        breakdown = evaluate_qubo_energy(model, bits)
        if breakdown.constraint_total < 0:
            raise ValueError("encoded constraint energy cannot be negative")
        if breakdown.constraint_total == 0 and not is_valid:
            raise ValueError("zero-penalty state decodes to an invalid M1 schedule")
        valid[index] = is_valid
        encoded_valid[index] = is_valid and breakdown.constraint_total == 0
        decoded_optimal[index] = is_valid and objective == reference.ground_energy
        optimal_valid[index] = (
            encoded_valid[index]
            and objective == reference.ground_energy
            and breakdown.business == objective
        )
        ground[index] = bitstring in reference.ground_bits
    actual_ground = min(exact_energies)
    actual_ground_strings = {
        bits
        for bits, energy in zip(serialized, exact_energies, strict=True)
        if energy == actual_ground
    }
    if actual_ground != reference.ground_energy or actual_ground_strings != set(
        reference.ground_bits
    ):
        raise ValueError(f"{reference.name}: saved ground set differs from exhaustive enumeration")
    valid_objectives = [objective for _, objective in cache.values() if objective is not None]
    if not valid_objectives or min(valid_objectives) != reference.ground_energy:
        raise ValueError(f"{reference.name}: independently decoded M1 optimum differs from ground")
    if not np.array_equal(ground, optimal_valid):
        raise ValueError("ground set differs from energy-consistent valid M1 optimum encodings")
    invalid_energies = [
        energy
        for energy, is_valid in zip(exact_energies, encoded_valid, strict=True)
        if not is_valid
    ]
    margin = None if not invalid_energies else min(invalid_energies) - actual_ground
    if margin != reference.constraint_energy_margin:
        raise ValueError("saved invalid-energy margin differs from exhaustive enumeration")
    for array in (energies, valid, encoded_valid, optimal_valid, decoded_optimal, ground):
        array.flags.writeable = False
    return StateTable(
        energies=energies,
        valid=valid,
        encoded_valid=encoded_valid,
        optimal_valid=optimal_valid,
        decoded_optimal=decoded_optimal,
        ground=ground,
        bits=tuple(serialized),
        objectives=tuple(objectives),
        max_energy=max(exact_energies),
        classical_objective=min(valid_objectives),
        checked_ground_set=True,
        reference_hash=reference.reference_hash,
    )


def analysis_for_probabilities(
    reference: FrozenReference,
    table: StateTable,
    probabilities: NDArray[np.float64] | list[float],
    expected_energy: float,
) -> dict[str, Any]:
    """Analyze a complete ideal or empirical distribution without repairing states."""
    if not table.checked_ground_set or table.reference_hash != reference.reference_hash:
        raise ValueError("state table is not checked against this frozen reference")
    values = np.asarray(probabilities, dtype=np.float64)
    if values.ndim != 1 or values.shape != table.energies.shape:
        raise ValueError("probabilities must have one value per basis state")
    if not np.all(np.isfinite(values)) or np.any(values < 0):
        raise ValueError("probabilities must be finite and nonnegative")
    if not np.isclose(float(values.sum()), 1.0, rtol=0, atol=1e-10):
        raise ValueError("probabilities must sum to one")
    if not isfinite(expected_energy):
        raise ValueError("supplied expected energy must be finite")
    expectation = float(np.dot(values, table.energies))
    difference = abs(expectation - expected_energy)
    tolerance = OPERATOR_ATOL + OPERATOR_RTOL * max(abs(expectation), abs(expected_energy))
    if difference > tolerance:
        raise ValueError("supplied expectation differs from complete offset-inclusive state energy")

    def mass(mask: NDArray[np.bool_]) -> float:
        return float(values[mask].sum())

    p_ground = mass(table.ground)
    p_optimal = mass(table.optimal_valid)
    if abs(p_ground - p_optimal) > 1e-12:
        raise ValueError("ground mass differs from energy-consistent M1 optimum mass")
    span = table.max_energy - reference.ground_energy
    residual = expectation - reference.ground_energy
    # Slight negative residuals can arise when a normalized floating distribution
    # is represented within tolerance; preserve the raw expectation separately.
    normalized = max(0.0, residual) / span if span else 0.0
    valid_probability = mass(table.valid)
    support = values > 0
    valid_objective_sum = sum(
        float(probability) * objective
        for probability, objective in zip(values, table.objectives, strict=True)
        if objective is not None
    )
    supported_objectives = [
        objective
        for objective, has_support in zip(table.objectives, support, strict=True)
        if objective is not None and has_support
    ]
    uniform_ground = len(reference.ground_bits) / len(values)
    return {
        "expected_energy": expectation,
        "ground_energy": reference.ground_energy,
        "max_energy": table.max_energy,
        "energy_residual": residual,
        "normalized_energy_error": normalized,
        "normalized_quality": 1.0 - normalized,
        "ground_state_probability": p_ground,
        "uniform_ground_probability": uniform_ground,
        "ground_probability_lift": p_ground / uniform_ground,
        "valid_probability": valid_probability,
        "encoded_valid_probability": mass(table.encoded_valid),
        "optimal_valid_probability": p_optimal,
        "decoded_optimal_schedule_probability": mass(table.decoded_optimal),
        "invalid_probability": mass(~table.valid),
        "encoding_invalid_probability": mass(~table.encoded_valid),
        "expected_valid_objective": (
            valid_objective_sum / valid_probability if valid_probability else None
        ),
        "best_valid_objective": min(supported_objectives) if supported_objectives else None,
        "checked_ground_set": table.checked_ground_set,
        "statevector_expectation_absolute_difference": difference,
        "expectation_verified": True,
    }
