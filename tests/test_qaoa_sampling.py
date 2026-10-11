"""Seeded final measurements and exhaustive treatment of every frozen minimum."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
from scipy.stats import binomtest

from km_quantum.domain import SchedulingInstance
from km_quantum.experiments.io import canonical_hash
from km_quantum.qaoa.analysis import build_state_table
from km_quantum.qaoa.ordering import m2_bits_to_qiskit_bitstring
from km_quantum.qaoa.references import FrozenReference, load_references
from km_quantum.qaoa.sampling import SAMPLING_TAIL_ALPHA, analyze_counts, sample_state
from km_quantum.qaoa.simulation import uniform_initial_state
from km_quantum.qubo.encoder import encode_qubo
from km_quantum.qubo.ising import qubo_to_ising

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def references() -> dict[str, FrozenReference]:
    return load_references(ROOT)


def test_uniform_state_samples_repeat_and_binomial_audit_uses_exact_mass(
    references: dict[str, FrozenReference],
) -> None:
    reference = references["A_single_job"]
    table = build_state_table(reference)
    circuit = uniform_initial_state(reference.qubo.number_variables)
    probability = len(reference.ground_bits) / len(table.bits)
    first = sample_state(
        reference, circuit, table, shots=1024, seed=919, exact_ground_probability=probability
    )
    second = sample_state(
        reference, circuit, table, shots=1024, seed=919, exact_ground_probability=probability
    )
    assert first == second
    third = sample_state(
        reference, circuit, table, shots=1024, seed=920, exact_ground_probability=probability
    )
    assert third["counts"] != first["counts"]
    assert sum(first["counts"].values()) == 1024
    ground_keys = {m2_bits_to_qiskit_bitstring(bits) for bits in reference.ground_bits}
    expected_ground_count = sum(
        count for key, count in first["counts"].items() if key in ground_keys
    )
    assert first["ground_state_samples"] == expected_ground_count
    assert first["empirical_ground_frequency"] == expected_ground_count / 1024
    assert first["exact_ground_probability"] == probability
    assert (
        first["binomial_two_sided_pvalue"]
        == binomtest(expected_ground_count, 1024, probability).pvalue
    )
    assert first["sampling_verified"] == (first["binomial_two_sided_pvalue"] >= SAMPLING_TAIL_ALPHA)
    assert first["sampling_verified"]
    assert not first["hardware_used"] and not first["noise_used"]
    assert not circuit.cregs and not any(item.operation.name == "measure" for item in circuit.data)
    for row in first["state_samples"]:
        assert row["qiskit_bitstring"] == m2_bits_to_qiskit_bitstring(row["m2_bits"])


@pytest.mark.parametrize("name", ["H_workload", "K_bounded_slack", "I_multiple_optima"])
def test_all_distinct_frozen_minima_contribute_to_observed_ground_frequency(
    references: dict[str, FrozenReference], name: str
) -> None:
    reference = references[name]
    table = build_state_table(reference)
    assert len(reference.ground_bits) >= 2
    counts = {m2_bits_to_qiskit_bitstring(bits): 7 for bits in reference.ground_bits}
    report = analyze_counts(
        reference, table, counts, shots=sum(counts.values()), seed=3, exact_ground_probability=1.0
    )
    assert report["ground_state_samples"] == report["shots"]
    assert report["ground_state_observed"] and report["optimum_observed"]
    assert report["empirical_ground_frequency"] == report["empirical_feasibility_rate"] == 1
    assert report["encoded_valid_sample_count"] == report["valid_sample_count"] == report["shots"]
    assert report["best_sampled_energy"] == reference.ground_energy
    assert report["best_decoded_m1_objective"] == reference.ground_energy
    assert len(report["state_samples"]) == len(reference.ground_bits)
    assert all(
        row["ground"] and row["valid"] and row["encoded_valid"] for row in report["state_samples"]
    )
    assert report["sampling_verified"]


@pytest.mark.parametrize("name", ["H_workload", "K_bounded_slack"])
def test_decoded_optimal_schedule_with_bad_slack_is_kept_separate_from_ground_sample(
    references: dict[str, FrozenReference], name: str
) -> None:
    reference = references[name]
    table = build_state_table(reference)
    candidates = np.flatnonzero(table.decoded_optimal & ~table.encoded_valid)
    assert len(candidates) > 0
    index = int(candidates[0])
    displayed = m2_bits_to_qiskit_bitstring(table.bits[index])
    report = analyze_counts(
        reference, table, {displayed: 10}, shots=10, seed=0, exact_ground_probability=0.0
    )
    assert report["best_decoded_m1_objective"] == reference.ground_energy
    assert report["valid_sample_count"] == 10
    assert report["encoded_valid_sample_count"] == report["ground_state_samples"] == 0
    assert report["optimal_valid_sample_count"] == 0
    assert report["decoded_optimal_schedule_sample_count"] == 10
    assert report["decoded_optimal_schedule_observed"]
    assert report["empirical_decoded_optimal_schedule_frequency"] == 1
    assert not report["ground_state_observed"] and not report["optimum_observed"]
    assert report["best_sampled_energy"] > reference.ground_energy
    row = report["state_samples"][0]
    assert row["valid"] and not row["encoded_valid"] and not row["ground"]
    assert row["m2_bits"] == table.bits[index]


def test_endian_mapping_modal_ties_and_impossible_binomial_observation(
    references: dict[str, FrozenReference],
) -> None:
    reference = references["A_single_job"]
    table = build_state_table(reference)
    counts = {"01": 4, "10": 4}
    report = analyze_counts(reference, table, counts, shots=8, seed=0, exact_ground_probability=0.0)
    assert report["modal_bitstring"] == "01"
    assert report["modal_m2_bits"] == "10"
    assert report["ground_state_samples"] == 4
    assert report["binomial_two_sided_pvalue"] == 0.0
    assert not report["sampling_verified"]


@pytest.mark.parametrize(
    "counts,shots",
    [
        ({}, 1),
        ({"00": 0}, 1),
        ({"00": -1}, 1),
        ({"00": True}, 1),
        ({"00": 1.0}, 1),
        ({"00": 2}, 1),
        ({"0": 1}, 1),
        ({"0x": 1}, 1),
        ({"0 0": 1}, 1),
        ({2: 1}, 1),
        ({"00": 1, 2: 1}, 2),
    ],
)
def test_malformed_counts_are_rejected(
    references: dict[str, FrozenReference], counts: dict, shots: int
) -> None:
    reference = references["A_single_job"]
    with pytest.raises(ValueError):
        analyze_counts(
            reference,
            build_state_table(reference),
            counts,
            shots=shots,
            seed=0,
            exact_ground_probability=0.25,
        )


def test_sampler_rejects_unbound_measured_wrong_dimension_and_unchecked_table(
    references: dict[str, FrozenReference],
) -> None:
    reference = references["A_single_job"]
    table = build_state_table(reference)
    unbound = QuantumCircuit(2)
    unbound.rx(Parameter("theta"), 0)
    for circuit in (
        unbound,
        QuantumCircuit(1),
        uniform_initial_state(2).measure_all(inplace=False),
    ):
        with pytest.raises(ValueError):
            sample_state(reference, circuit, table, shots=16, seed=0, exact_ground_probability=0.25)
    for changed in (
        replace(table, reference_hash="wrong"),
        replace(table, checked_ground_set=False),
    ):
        with pytest.raises(ValueError, match="checked state table"):
            analyze_counts(
                reference, changed, {"00": 16}, shots=16, seed=0, exact_ground_probability=0.25
            )
    for probability in (-0.1, 1.1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="probability"):
            analyze_counts(
                reference, table, {"00": 16}, shots=16, seed=0, exact_ground_probability=probability
            )
    for shots, seed in ((0, 0), (True, 0), (16, -1), (16, True)):
        with pytest.raises(ValueError, match="integers"):
            sample_state(
                reference,
                uniform_initial_state(2),
                table,
                shots=shots,
                seed=seed,
                exact_ground_probability=0.25,
            )
    with pytest.raises(ValueError, match="supplied final circuit"):
        sample_state(
            reference,
            uniform_initial_state(2),
            table,
            shots=16,
            seed=0,
            exact_ground_probability=0.5,
        )


def test_zero_qubit_sampling_returns_the_empty_ground_assignment() -> None:
    instance = SchedulingInstance(instance_id="empty-sampling", seed=0, workers=(), jobs=())
    model = encode_qubo(instance)
    ising = qubo_to_ising(model)
    reference = FrozenReference(
        name="empty-sampling",
        instance=instance,
        qubo=model,
        ising=ising,
        ground_bits=("",),
        ground_energy=0,
        constraint_energy_margin=None,
        reference_hash="empty-sampling",
        qubo_hash=canonical_hash(model),
        ising_hash=canonical_hash(ising),
    )
    report = sample_state(
        reference,
        QuantumCircuit(0),
        build_state_table(reference),
        shots=17,
        seed=7,
        exact_ground_probability=1.0,
    )
    assert report["counts"] == {"": 17}
    assert report["modal_bitstring"] == report["modal_m2_bits"] == ""
    assert report["ground_state_samples"] == report["valid_sample_count"] == 17
    assert report["empirical_ground_frequency"] == report["empirical_feasibility_rate"] == 1
    assert report["best_sampled_energy"] == report["best_decoded_m1_objective"] == 0
    assert report["sampling_verified"]
