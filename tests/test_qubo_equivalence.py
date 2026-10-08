"""Full-state equivalence, canonical feasible extensions and Ising audits."""

import json
from itertools import product
from pathlib import Path

import pytest

from km_quantum.domain import Assignment, Schedule, SchedulingInstance
from km_quantum.experiments.io import canonical_hash
from km_quantum.metrics import evaluate_objective, validate_schedule
from km_quantum.qubo import decode_bitstring, encode_qubo, encode_schedule
from km_quantum.qubo.encoder import instance_fingerprint
from km_quantum.qubo.exhaustive import solve_qubo
from km_quantum.qubo.ising import IsingModel, bits_to_spins, qubo_to_ising
from km_quantum.qubo.models import QUBOConfig, QUBOModel, evaluate_qubo_energy
from km_quantum.qubo.references import (
    infeasible_case,
    m2_hand_cases,
    penalty_sensitivity_case,
    random_small_cases,
)
from km_quantum.qubo.verification import verify_equivalence

ROOT = Path(__file__).resolve().parents[1]
HAND_CASES = m2_hand_cases()
RANDOM_CASES = random_small_cases()


def _schedule_signature(schedule: Schedule) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    return (
        tuple(
            sorted((assignment.job_id, assignment.worker_id) for assignment in schedule.assignments)
        ),
        tuple(sorted(schedule.unassigned_job_ids)),
    )


def _feasible_schedules(instance: SchedulingInstance):
    for choices in product(range(-1, len(instance.workers)), repeat=len(instance.jobs)):
        schedule = Schedule(
            instance_id=instance.instance_id,
            assignments=tuple(
                Assignment(job_id=job.id, worker_id=instance.workers[choice].id)
                for job, choice in zip(instance.jobs, choices, strict=True)
                if choice >= 0
            ),
            unassigned_job_ids=tuple(
                job.id for job, choice in zip(instance.jobs, choices, strict=True) if choice == -1
            ),
        )
        if validate_schedule(instance, schedule).is_valid:
            yield schedule


@pytest.mark.parametrize("name", list(HAND_CASES))
def test_hand_reference_all_ground_states_match_manual_optimum(name: str) -> None:
    instance, config = HAND_CASES[name]
    model = encode_qubo(instance, config)
    reference = instance.metadata["reference"]
    assert isinstance(reference, dict)
    expected = reference["expected_objective"]
    verification = verify_equivalence(instance, config=config, model=model)
    assert verification.status == "PASS", verification.model_dump()
    assert verification.equivalence_passed
    result = solve_qubo(model, instance)
    assert result.explored_states == result.state_count == 2**model.number_variables
    assert result.ground_states
    assert result.ground_state_degeneracy == len(result.ground_states)
    for ground in result.ground_states:
        assert ground.validation.is_valid
        assert ground.encoded_feasible
        assert ground.business_objective == expected
        assert ground.breakdown.business == expected
        assert ground.breakdown.constraint_total == 0
        assert ground.energy == expected
    assert result.invalid_energy_margin is None or result.invalid_energy_margin > 0
    assert sum(level.degeneracy for level in result.spectrum) == result.state_count


@pytest.mark.parametrize("name", list(RANDOM_CASES))
def test_frozen_random_small_instance_full_equivalence(name: str) -> None:
    instance, config = RANDOM_CASES[name]
    model = encode_qubo(instance, config)
    assert 1 <= len(instance.workers) <= 3
    assert 1 <= len(instance.jobs) <= 4
    assert model.number_variables <= 16
    verification = verify_equivalence(instance, config=config, model=model)
    assert verification.status == "PASS", (name, verification.model_dump())
    assert verification.equivalence_passed


@pytest.mark.parametrize("name", [*HAND_CASES, *RANDOM_CASES])
def test_preprocessing_preserves_every_feasible_schedule_and_exact_ranking(name: str) -> None:
    instance, config = (HAND_CASES if name in HAND_CASES else RANDOM_CASES)[name]
    model = encode_qubo(instance, config)
    objective_energy_pairs = []
    for schedule in _feasible_schedules(instance):
        # Success for every original feasible choice proves no preprocessing loss.
        bits = encode_schedule(instance, model.mapping, schedule)
        decoded = decode_bitstring(instance, model.mapping, bits)
        assert _schedule_signature(decoded) == _schedule_signature(schedule)
        assert validate_schedule(instance, decoded).is_valid
        objective = evaluate_objective(instance, schedule, config.objective).total
        breakdown = evaluate_qubo_energy(model, bits)
        assert breakdown.constraint_total == 0
        assert breakdown.total == breakdown.business == objective
        assert objective <= model.penalties.business_upper_bound
        objective_energy_pairs.append((objective, breakdown.total))
    assert objective_energy_pairs
    assert sorted(objective_energy_pairs) == sorted(
        objective_energy_pairs, key=lambda pair: pair[1]
    )


@pytest.mark.parametrize("name", list(HAND_CASES))
def test_ising_equals_qubo_for_every_hand_reference_bitstring(name: str) -> None:
    instance, config = HAND_CASES[name]
    model = encode_qubo(instance, config)
    ising = qubo_to_ising(model)
    for bits in product((0, 1), repeat=model.number_variables):
        # Scaled integer comparison is exact, including the constant offset.
        assert ising.energy4(bits_to_spins(bits)) == 4 * model.energy(bits)


def test_imbalance_minimum_over_auxiliaries_preserves_each_feasible_primary_choice() -> None:
    instance, config = HAND_CASES["M_active_imbalance"]
    model = encode_qubo(instance, config)
    prefix = model.mapping.primary_variable_count
    minimum_by_primary = {}
    for bits in product((0, 1), repeat=model.number_variables):
        primary = bits[:prefix]
        energy = model.energy(bits)
        minimum_by_primary[primary] = min(minimum_by_primary.get(primary, energy), energy)
    for schedule in _feasible_schedules(instance):
        canonical = encode_schedule(instance, model.mapping, schedule)
        expected = evaluate_objective(instance, schedule, config.objective).total
        assert minimum_by_primary[canonical[:prefix]] == expected
    # Changing auxiliaries is observable in energy, but does not repair/change assignments.
    first = next(_feasible_schedules(instance))
    canonical = encode_schedule(instance, model.mapping, first)
    changed = canonical[:prefix] + tuple(1 - bit for bit in canonical[prefix:])
    assert _schedule_signature(decode_bitstring(instance, model.mapping, changed)) == (
        _schedule_signature(first)
    )


@pytest.mark.parametrize(
    "scale, passed", [("1/4", False), ("1/2", False), ("1", True), ("2", True), ("4", True)]
)
def test_penalty_sensitivity_detects_and_restores_equivalence(scale: str, passed: bool) -> None:
    instance, unsafe_config = penalty_sensitivity_case()
    config = QUBOConfig(**{**unsafe_config.model_dump(), "penalty_scale": scale})
    verification = verify_equivalence(instance, config=config)
    assert verification.equivalence_passed is passed
    assert verification.status == ("PASS" if passed else "FAIL")
    result = solve_qubo(encode_qubo(instance, config), instance)
    assert any(not ground.validation.is_valid for ground in result.ground_states) is not passed
    assert result.ground_energy == 100 if passed else result.ground_energy < 100


def test_original_infeasibility_is_not_equivalence_pass() -> None:
    instance, config = infeasible_case()
    verification = verify_equivalence(instance, config=config)
    assert verification.status == "INFEASIBLE"
    assert not verification.equivalence_passed
    result = solve_qubo(encode_qubo(instance, config), instance)
    assert all(not ground.validation.is_valid for ground in result.ground_states)


def test_reference_fixtures_match_frozen_construction() -> None:
    fixture_root = ROOT / "data" / "synthetic" / "m2"
    assert {path.stem for path in (fixture_root / "hand_cases").glob("*.json")} == set(HAND_CASES)
    for name, (instance, _) in HAND_CASES.items():
        raw = (fixture_root / "hand_cases" / f"{name}.json").read_text(encoding="utf-8")
        assert raw == instance.model_dump_json(indent=2) + "\n"
        assert SchedulingInstance.model_validate_json(raw) == instance
    raw_random = json.loads((fixture_root / "random_cases.json").read_text(encoding="utf-8"))
    expected_random = {
        name: {
            "instance": instance.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
        }
        for name, (instance, config) in RANDOM_CASES.items()
    }
    assert raw_random == expected_random
    assert len(RANDOM_CASES) == 100
    assert list(RANDOM_CASES) == [f"random-{seed:03d}" for seed in range(100)]
    assert random_small_cases() == RANDOM_CASES


def test_frozen_qubo_and_ising_snapshots_have_no_encoding_drift() -> None:
    """Re-encode all frozen inputs/configs; do not enumerate binary states again."""
    fixture_root = ROOT / "data" / "synthetic" / "m2"
    inputs = {}
    for path in sorted((fixture_root / "hand_cases").glob("*.json")):
        instance = SchedulingInstance.model_validate_json(path.read_text(encoding="utf-8"))
        inputs[path.stem] = (
            instance,
            QUBOConfig.model_validate(instance.metadata["reference"]["qubo_config"]),
        )
    raw_random = json.loads((fixture_root / "random_cases.json").read_text(encoding="utf-8"))
    inputs.update(
        {
            name: (
                SchedulingInstance.model_validate(pair["instance"]),
                QUBOConfig.model_validate(pair["config"]),
            )
            for name, pair in raw_random.items()
        }
    )
    lines = (fixture_root / "frozen_qubos.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == len(inputs) == 114
    assert [record["name"] for record in records] == sorted(inputs)
    for line, record in zip(lines, records, strict=True):
        assert line == json.dumps(
            record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        assert record["schema_version"] == 1
        instance, config = inputs[record["name"]]
        qubo = encode_qubo(instance, config)
        ising = qubo_to_ising(qubo)
        assert record["instance_fingerprint"] == instance_fingerprint(instance)
        assert record["qubo_hash"] == canonical_hash(qubo)
        assert record["ising_hash"] == canonical_hash(ising)
        assert record["qubo_model"] == qubo.model_dump(mode="json")
        assert record["ising_model"] == ising.model_dump(mode="json")
        assert QUBOModel.model_validate(record["qubo_model"]) == qubo
        assert IsingModel.model_validate(record["ising_model"]) == ising
