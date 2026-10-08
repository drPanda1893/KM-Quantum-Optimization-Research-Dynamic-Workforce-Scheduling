"""Independent checks of encoding algebra, constraints and canonical mapping."""

from fractions import Fraction
from itertools import product
from math import ceil
from pathlib import Path

import pytest

from km_quantum.domain import Assignment, Job, Schedule, SchedulingInstance, TimeWindow, Worker
from km_quantum.metrics import validate_schedule
from km_quantum.qubo import decode_bitstring, encode_qubo, encode_schedule
from km_quantum.qubo.models import (
    PolynomialBuilder,
    QUBOConfig,
    QUBOModel,
    evaluate_qubo_energy,
    normalize_bits,
)
from km_quantum.qubo.references import m2_hand_cases


def test_square_expansion_is_exact_for_every_binary_state() -> None:
    coefficients = {0: 3, 1: -2, 2: 4}
    builder = PolynomialBuilder()
    builder.add_square(coefficients, constant=-5, penalty=11)
    polynomial = builder.freeze(3)
    for bits in product((0, 1), repeat=3):
        direct = 11 * (-5 + sum(coefficients[index] * bit for index, bit in enumerate(bits))) ** 2
        assert polynomial.energy(bits) == direct
        matrix = polynomial.to_matrix()
        assert (
            sum(
                bits[left] * matrix[left][right] * bits[right]
                for left in range(3)
                for right in range(3)
            )
            == direct - polynomial.constant
        )


def test_coverage_penalty_all_states() -> None:
    instance, config = m2_hand_cases()["I_multiple_optima"]
    model = encode_qubo(instance, config)
    primary = [v.index for v in model.mapping.variables if v.job_id == "job-a"]
    assert len(primary) == 3
    for bits in product((0, 1), repeat=model.number_variables):
        expected = (
            model.penalties.applied_penalty * (sum(bits[index] for index in primary) - 1) ** 2
        )
        assert evaluate_qubo_energy(model, bits).coverage == expected


def test_mandatory_penalty_all_states() -> None:
    instance, config = m2_hand_cases()["A_single_job"]
    model = encode_qubo(instance, config)
    skipped = next(v.index for v in model.mapping.variables if v.kind == "unassigned")
    for bits in product((0, 1), repeat=model.number_variables):
        assert evaluate_qubo_energy(model, bits).mandatory == (
            model.penalties.applied_penalty * bits[skipped]
        )


def test_overlap_penalty_all_states_without_double_pairs() -> None:
    instance, config = m2_hand_cases()["E_overlap"]
    model = encode_qubo(instance, config)
    assignment_indices = [v.index for v in model.mapping.variables if v.kind == "assignment"]
    assert len(assignment_indices) == 2
    for bits in product((0, 1), repeat=model.number_variables):
        expected = model.penalties.applied_penalty
        expected *= bits[assignment_indices[0]] * bits[assignment_indices[1]]
        assert evaluate_qubo_energy(model, bits).overlap == expected
    overlap = model.components["overlap"]
    assert len(overlap.quadratic) == 1
    assert overlap.quadratic[0].coefficient == model.penalties.applied_penalty


@pytest.mark.parametrize("name", ["H_workload", "K_bounded_slack"])
def test_workload_slack_equation_all_states(name: str) -> None:
    instance, config = m2_hand_cases()[name]
    model = encode_qubo(instance, config)
    group = next(g for g in model.mapping.integers if g.role == "workload_slack")
    jobs = {job.id: job for job in instance.jobs}
    assignments = [v for v in model.mapping.variables if v.kind == "assignment"]
    reachable = {
        sum(
            model.mapping.variables[index].weight * bit
            for index, bit in zip(group.indices, choice, strict=True)
        )
        for choice in product((0, 1), repeat=len(group.indices))
    }
    assert reachable == set(range(group.upper_bound + 1))
    for bits in product((0, 1), repeat=model.number_variables):
        load = sum(jobs[v.job_id].duration_minutes * bits[v.index] for v in assignments)
        slack = sum(model.mapping.variables[index].weight * bits[index] for index in group.indices)
        expected = (
            model.penalties.applied_penalty * (load // group.unit + slack - group.upper_bound) ** 2
        )
        assert evaluate_qubo_energy(model, bits).workload == expected


def test_duration_gcd_normalizes_exact_capacity_not_rounded_up() -> None:
    instance = SchedulingInstance(
        instance_id="gcd-floor-capacity",
        seed=0,
        workers=(
            Worker(
                id="worker-a",
                skills=frozenset(),
                availability=(TimeWindow(start_minute=0, end_minute=90),),
                max_work_minutes=75,
            ),
        ),
        jobs=(
            Job(id="job-30", required_skills=frozenset(), start_minute=0, end_minute=30),
            Job(id="job-60", required_skills=frozenset(), start_minute=30, end_minute=90),
        ),
    )
    model = encode_qubo(instance)
    group = next(g for g in model.mapping.integers if g.role == "workload_slack")
    assert group.unit == 30
    assert group.upper_bound == 2  # floor(75/30), preserving exactly the attainable loads.
    schedule = Schedule(
        instance_id=instance.instance_id,
        assignments=(Assignment(worker_id="worker-a", job_id="job-60"),),
        unassigned_job_ids=("job-30",),
    )
    bits = encode_schedule(instance, model.mapping, schedule)
    assert evaluate_qubo_energy(model, bits).constraint_total == 0
    selected = tuple(
        1 if variable.kind == "assignment" else bit
        for variable, bit in zip(model.mapping.variables, bits, strict=True)
    )
    assert evaluate_qubo_energy(model, selected).workload > 0


def test_nonbinding_workload_needs_no_slack_bits() -> None:
    instance, config = m2_hand_cases()["A_single_job"]
    model = encode_qubo(instance, config)
    assert not any(g.role == "workload_slack" for g in model.mapping.integers)
    assert model.number_variables == 2


def test_documented_mini_example_complete_matrix_and_ising_coefficients() -> None:
    from km_quantum.qubo.ising import qubo_to_ising

    fixture = Path(__file__).resolve().parents[1] / "data/synthetic/m2/mini_example.json"
    instance = SchedulingInstance.model_validate_json(fixture.read_text(encoding="utf-8"))
    model = encode_qubo(
        instance,
        QUBOConfig(objective={"unassigned_penalty": 10, "cost_weight": 1}),
    )
    assert model.number_variables == 4
    assert model.penalties.business_upper_bound == 20
    assert model.penalties.applied_penalty == 21
    assert model.constant == 42
    assert model.to_matrix() == [
        [-19, 0, 42, 0],
        [0, -18, 0, 42],
        [0, 0, -11, 0],
        [0, 0, 0, -11],
    ]
    assert model.energy("1100") == 5
    ising = qubo_to_ising(model)
    assert ising.constant4 == 134
    assert [term.coefficient for term in ising.fields] == [-4, -6, -20, -20]
    assert [(term.left, term.right, term.coefficient) for term in ising.couplings] == [
        (0, 2, 42),
        (1, 3, 42),
    ]


def test_statically_impossible_assignments_are_removed() -> None:
    for name, expected_reason in [
        ("C_skill_compatibility", "skill"),
        ("D_availability", "availability"),
    ]:
        instance, config = m2_hand_cases()[name]
        model = encode_qubo(instance, config)
        assert len(model.mapping.removed_assignments) == 1
        assert model.mapping.removed_assignments[0].reason == expected_reason
    instance, _ = m2_hand_cases()["A_single_job"]
    worker = instance.workers[0].model_copy(update={"max_work_minutes": 0})
    tiny_capacity = instance.model_copy(update={"workers": (worker,)})
    model = encode_qubo(tiny_capacity)
    assert len(model.mapping.removed_assignments) == 1
    assert model.mapping.removed_assignments[0].reason == "individual_capacity"
    assert all(v.kind != "assignment" for v in model.mapping.variables)


def test_decoder_preserves_missing_and_duplicate_coverage() -> None:
    instance, config = m2_hand_cases()["I_multiple_optima"]
    model = encode_qubo(instance, config)
    missing = decode_bitstring(instance, model.mapping, "0" * model.number_variables)
    assert missing.assignments == missing.unassigned_job_ids == ()
    assert "missing_job" in {v.code for v in validate_schedule(instance, missing).violations}
    duplicate_bits = tuple(int(v.kind == "assignment") for v in model.mapping.variables)
    duplicate = decode_bitstring(instance, model.mapping, duplicate_bits)
    assert len(duplicate.assignments) == 2
    assert not duplicate.unassigned_job_ids
    assert "duplicate_job" in {v.code for v in validate_schedule(instance, duplicate).violations}


def test_mapping_and_coefficients_are_deterministic_under_input_permutation() -> None:
    instance, config = m2_hand_cases()["J_combined_constraints"]
    first = encode_qubo(instance, config)
    second = encode_qubo(
        instance.model_copy(
            update={"workers": instance.workers[::-1], "jobs": instance.jobs[::-1]}
        ),
        config,
    )
    assert first.mapping.variables == second.mapping.variables
    assert first.mapping.integers == second.mapping.integers
    assert first.linear == second.linear
    assert first.quadratic == second.quadratic
    assert first.constant == second.constant
    assert first.model_dump_json() == encode_qubo(instance, config).model_dump_json()
    assert (
        QUBOModel.model_validate_json(first.model_dump_json()).model_dump_json()
        == first.model_dump_json()
    )


def test_penalty_scaling_uses_exact_rational_ceiling() -> None:
    instance, config = m2_hand_cases()["G_priority"]
    for scale in ("1/4", "1/2", "1", "2", "4"):
        scaled_config = QUBOConfig(**{**config.model_dump(), "penalty_scale": scale})
        model = encode_qubo(instance, scaled_config)
        assert model.penalties.applied_penalty == ceil(
            model.penalties.safe_penalty * Fraction(scale)
        )
        assert model.penalties.safety_guaranteed == (
            model.penalties.applied_penalty > model.penalties.business_upper_bound
        )


@pytest.mark.parametrize("bits", ["2", "", (True,), (0.0,), (2,), (0, 1)])
def test_invalid_bits_are_rejected(bits: object) -> None:
    with pytest.raises(ValueError):
        normalize_bits(bits, 1)
