"""Independent enumeration checks for ground tracking, spectrum and proof semantics."""

from collections import Counter
from itertools import product

import pytest
from pydantic import ValidationError

from km_quantum.domain import (
    Job,
    ObjectiveWeights,
    SchedulingInstance,
    SolverResult,
    TimeWindow,
    Worker,
)
from km_quantum.metrics import validate_schedule
from km_quantum.qubo import verification as verification_module
from km_quantum.qubo.decoder import decode_bitstring, encode_schedule
from km_quantum.qubo.encoder import encode_qubo
from km_quantum.qubo.exhaustive import QUBOSolverResult, _energy_states, solve_qubo
from km_quantum.qubo.models import QUBOConfig, evaluate_qubo_energy
from km_quantum.qubo.references import infeasible_case, m2_hand_cases, penalty_sensitivity_case
from km_quantum.qubo.verification import (
    VerificationResult,
    verify_equivalence,
    verify_ising_equivalence,
)


@pytest.mark.parametrize("name", list(m2_hand_cases()))
def test_gray_updates_and_minima_match_direct_all_state_enumeration(name: str) -> None:
    instance, config = m2_hand_cases()[name]
    model = encode_qubo(instance, config)
    direct = {
        "".join(map(str, bits)): model.energy(bits)
        for bits in product((0, 1), repeat=model.number_variables)
    }
    seen = set()
    for _, bits, energy, components in _energy_states(model, 1 << model.number_variables):
        state = "".join(map(str, bits))
        seen.add(state)
        assert energy == direct[state]
        assert components == {
            name: polynomial.energy(bits) for name, polynomial in model.components.items()
        }
    assert seen == set(direct)
    result = solve_qubo(model, instance)
    histogram = Counter(direct.values())
    minimum = min(histogram)
    expected_grounds = sorted(bits for bits, energy in direct.items() if energy == minimum)
    energies = sorted(histogram)
    assert result.ground_energy == minimum
    assert [ground.bits for ground in result.ground_states] == expected_grounds
    assert result.ground_state_degeneracy == histogram[minimum]
    assert [(level.energy, level.degeneracy) for level in result.spectrum] == sorted(
        histogram.items()
    )
    assert result.first_excited_energy == (energies[1] if len(energies) > 1 else None)
    assert result.first_excited_gap == (energies[1] - minimum if len(energies) > 1 else None)
    assert result.explored_states == result.state_count == len(direct)
    assert result.enumeration_complete and result.proven_ground_state
    assert result.encoding_valid_state_count + result.encoding_invalid_state_count == len(direct)
    assert result.schedule_valid_state_count + result.schedule_invalid_state_count == len(direct)
    assert QUBOSolverResult.model_validate_json(result.model_dump_json()) == result


def test_bounded_slack_degeneracy_retains_every_ground_bitstring() -> None:
    instance, config = m2_hand_cases()["K_bounded_slack"]
    result = solve_qubo(encode_qubo(instance, config), instance)
    assert result.ground_state_degeneracy == 2
    assert len({ground.bits for ground in result.ground_states}) == 2
    assert len({ground.schedule.assignments for ground in result.ground_states}) == 1
    assert all(
        ground.encoded_feasible and ground.objective_matches for ground in result.ground_states
    )


def test_auxiliary_invalid_states_are_not_treated_as_valid_schedules() -> None:
    instance, config = m2_hand_cases()["M_active_imbalance"]
    model = encode_qubo(instance, config)
    result = solve_qubo(model, instance)
    assert result.schedule_valid_auxiliary_invalid_state_count > 0
    assert result.schedule_valid_state_count > result.encoding_valid_state_count
    assert result.schedule_valid_noncanonical_state_count > 0
    invalid_energies = []
    auxiliary_invalid = 0
    noncanonical = 0
    for bits in product((0, 1), repeat=model.number_variables):
        breakdown = evaluate_qubo_energy(model, bits)
        valid_schedule = validate_schedule(
            instance, decode_bitstring(instance, model.mapping, bits)
        ).is_valid
        encoded_valid = valid_schedule and breakdown.constraint_total == 0
        if not encoded_valid:
            invalid_energies.append(breakdown.total)
        if valid_schedule and breakdown.constraint_total:
            auxiliary_invalid += 1
        if valid_schedule and breakdown.constraint_total == 0:
            canonical = encode_schedule(
                instance, model.mapping, decode_bitstring(instance, model.mapping, bits)
            )
            noncanonical += model.components["business"].energy(canonical) != breakdown.business
    assert result.schedule_valid_auxiliary_invalid_state_count == auxiliary_invalid
    assert result.schedule_valid_noncanonical_state_count == noncanonical
    assert result.best_invalid_energy == min(invalid_energies)
    assert result.invalid_energy_margin == min(invalid_energies) - result.ground_energy > 0


def test_spectrum_can_be_omitted_without_changing_ground_proof() -> None:
    instance, config = m2_hand_cases()["E_overlap"]
    model = encode_qubo(instance, config)
    detailed = solve_qubo(model, instance)
    compact = solve_qubo(model, instance, analyze_spectrum=False)
    assert compact.spectrum == ()
    assert not compact.spectrum_analyzed
    assert compact.ground_states == detailed.ground_states
    assert compact.ground_state_degeneracy == detailed.ground_state_degeneracy
    assert compact.first_excited_gap == detailed.first_excited_gap
    assert compact.invalid_energy_margin == detailed.invalid_energy_margin
    assert compact.explored_states == compact.state_count


@pytest.mark.parametrize("max_states", [0, -1, True, 2.5])
def test_state_cap_requires_positive_integer(max_states: int) -> None:
    instance, config = m2_hand_cases()["A_single_job"]
    with pytest.raises(ValueError, match="positive integer"):
        solve_qubo(encode_qubo(instance, config), instance, max_states=max_states)


def test_state_cap_rejects_before_returning_a_truncated_minimum() -> None:
    instance, config = m2_hand_cases()["M_active_imbalance"]
    model = encode_qubo(instance, config)
    with pytest.raises(ValueError, match="exceed max_states"):
        solve_qubo(model, instance, max_states=(1 << model.number_variables) - 1)
    with pytest.raises(ValueError, match="exceed max_states"):
        verify_equivalence(instance, model=model, max_states=1)
    with pytest.raises(ValueError, match="exceed max_states"):
        verify_ising_equivalence(model, max_states=1)


def test_infeasible_original_does_not_become_an_equivalence_pass() -> None:
    instance, config = infeasible_case()
    result = verify_equivalence(instance, config)
    assert result.status == "INFEASIBLE"
    assert not result.equivalence_passed
    assert result.original_feasible is False
    assert result.classical_objective is None
    assert result.qubo_result.proven_ground_state
    assert result.qubo_result.encoding_valid_state_count == 0
    assert result.qubo_result.best_valid_energy is None
    assert all(not ground.encoded_feasible for ground in result.qubo_result.ground_states)


def test_unsafe_penalty_fails_with_invalid_ground_and_no_positive_margin() -> None:
    instance, config = penalty_sensitivity_case()
    result = verify_equivalence(instance, config)
    assert result.status == "FAIL"
    assert not result.equivalence_passed
    assert result.original_feasible is True
    assert result.classical_objective == 100
    assert result.qubo_result.ground_energy < 100
    assert result.qubo_result.best_valid_energy == 100
    assert result.qubo_result.invalid_energy_margin == 0
    assert not result.strict_invalid_margin


def test_tied_invalid_ground_cannot_hide_behind_a_valid_ground() -> None:
    instance, config = penalty_sensitivity_case()
    config = config.model_copy(update={"penalty_scale": "100/101"})
    model = encode_qubo(instance, config)
    assert model.penalties.applied_penalty == 100
    result = verify_equivalence(instance, model=model)
    assert result.status == "FAIL"
    assert result.qubo_result.ground_energy == result.classical_objective == 100
    assert any(ground.encoded_feasible for ground in result.qubo_result.ground_states)
    assert any(not ground.encoded_feasible for ground in result.qubo_result.ground_states)
    assert result.qubo_result.invalid_energy_margin == 0


def test_empirical_weak_penalty_pass_does_not_claim_general_safety() -> None:
    instance, config = m2_hand_cases()["E_overlap"]
    config = config.model_copy(update={"penalty_scale": "1/2"})
    model = encode_qubo(instance, config)
    assert not model.penalties.safety_guaranteed
    result = verify_equivalence(instance, model=model)
    assert result.status == "PASS"
    assert result.equivalence_passed
    assert result.strict_invalid_margin
    assert result.qubo_result.invalid_energy_margin == 1


def test_missing_classical_proof_is_inconclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    instance, config = m2_hand_cases()["A_single_job"]
    unknown = SolverResult(
        status="UNKNOWN", schedule=None, objective_value=None, runtime_ms=0, proven_optimal=False
    )
    monkeypatch.setattr(verification_module, "solve_cp_sat", lambda *_: unknown)
    result = verify_equivalence(instance, config)
    assert result.status == "INCONCLUSIVE"
    assert not result.equivalence_passed
    assert any("Both original references" in reason for reason in result.reasons)


def test_full_ising_check_reports_every_offset_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    instance, config = m2_hand_cases()["E_overlap"]
    model = encode_qubo(instance, config)
    original = verification_module.qubo_to_ising
    monkeypatch.setattr(
        verification_module,
        "qubo_to_ising",
        lambda model: original(model).model_copy(
            update={"constant4": original(model).constant4 + 4}
        ),
    )
    result = verify_ising_equivalence(model)
    assert not result.equivalent
    assert result.mismatch_count == result.checked_states == 1 << model.number_variables
    assert result.maximum_absolute_difference4 == 4


def test_empty_instance_has_one_ground_and_no_excitation() -> None:
    instance = SchedulingInstance(instance_id="empty", seed=0, workers=(), jobs=())
    model = encode_qubo(instance)
    result = verify_equivalence(instance, model=model)
    assert result.status == "PASS"
    assert result.qubo_result.state_count == result.qubo_result.ground_state_degeneracy == 1
    assert result.qubo_result.ground_states[0].bits == ""
    assert result.qubo_result.first_excited_energy is None
    assert result.qubo_result.first_excited_gap is None
    assert result.qubo_result.invalid_energy_margin is None
    assert result.strict_invalid_margin
    assert result.canonical_encoding_checked_count == 1
    assert VerificationResult.model_validate_json(result.model_dump_json()) == result


def test_instance_reordering_preserves_mapping_identity_and_enumerated_result() -> None:
    instance, config = m2_hand_cases()["B_two_workers_cost"]
    model = encode_qubo(instance, config)
    reordered = instance.model_copy(update={"workers": tuple(reversed(instance.workers))})
    first = solve_qubo(model, instance)
    second = solve_qubo(model, reordered)
    assert first.ground_states == second.ground_states
    assert first.spectrum == second.spectrum


def test_instance_or_configuration_mismatch_is_rejected() -> None:
    instance, config = m2_hand_cases()["A_single_job"]
    model = encode_qubo(instance, config)
    with pytest.raises(ValueError, match="supplied instance"):
        solve_qubo(model, instance.model_copy(update={"seed": 1}))
    with pytest.raises(ValueError, match="config differs"):
        verify_equivalence(instance, config.model_copy(update={"penalty_scale": "2"}), model=model)


def test_supplied_tampered_polynomial_is_rejected_before_verification() -> None:
    instance, config = m2_hand_cases()["A_single_job"]
    model = encode_qubo(instance, config)
    components = dict(model.components)
    components["business"] = components["business"].model_copy(update={"constant": 1})
    tampered = model.model_copy(update={"constant": model.constant + 1, "components": components})
    with pytest.raises(ValueError, match="canonical encoding"):
        verify_equivalence(instance, model=tampered)


def test_serialization_cannot_turn_an_unsafe_failure_into_pass() -> None:
    instance, config = penalty_sensitivity_case()
    report = verify_equivalence(instance, config)
    data = report.model_dump(mode="json")
    data.update(status="PASS", equivalence_passed=True, reasons=[])
    with pytest.raises(ValidationError, match="PASS requires"):
        VerificationResult.model_validate(data)


def test_serialization_rejects_incomplete_enumeration_claim_and_fake_validation_count() -> None:
    instance, config = m2_hand_cases()["A_single_job"]
    result = solve_qubo(encode_qubo(instance, config), instance)
    data = result.model_dump(mode="json")
    data["enumeration_complete"] = False
    with pytest.raises(ValidationError):
        QUBOSolverResult.model_validate(data)
    data = result.model_dump(mode="json")
    data["ground_states"][0]["validation"]["violation_count"] = 1
    with pytest.raises(ValidationError, match="violation_count is inconsistent"):
        QUBOSolverResult.model_validate(data)


def test_capacity_not_divisible_by_duration_gcd_is_encoded_exactly() -> None:
    instance = SchedulingInstance(
        instance_id="gcd-capacity",
        seed=0,
        workers=(
            Worker(
                id="w",
                skills=frozenset({"a"}),
                availability=(TimeWindow(start_minute=0, end_minute=10),),
                max_work_minutes=7,
                cost_per_hour=31,
            ),
        ),
        jobs=(
            Job(id="a", required_skills=frozenset({"a"}), start_minute=0, end_minute=4),
            Job(
                id="b", required_skills=frozenset({"a"}), start_minute=4, end_minute=10, priority=2
            ),
        ),
    )
    config = QUBOConfig(objective=ObjectiveWeights(unassigned_penalty=17, cost_weight=2))
    model = encode_qubo(instance, config)
    slack = next(group for group in model.mapping.integers if group.role == "workload_slack")
    assert slack.unit == 2
    assert slack.upper_bound == 3  # floor(7/2), not an incorrectly rounded-up capacity.
    result = verify_equivalence(instance, model=model)
    assert result.status == "PASS"
    assert result.classical_objective == 25
    assert result.canonical_encoding_checked_count == 3  # omit both, assign a, assign b.


def test_mixed_duration_global_gcd_preserves_cost_and_idle_worker_imbalance() -> None:
    availability = (TimeWindow(start_minute=0, end_minute=10),)
    instance = SchedulingInstance(
        instance_id="gcd-imbalance",
        seed=0,
        workers=(
            Worker(
                id="w-a",
                skills=frozenset({"a"}),
                availability=availability,
                max_work_minutes=7,
                cost_per_hour=61,
            ),
            Worker(
                id="w-b",
                skills=frozenset({"b"}),
                availability=availability,
                max_work_minutes=9,
                cost_per_hour=97,
            ),
            Worker(id="idle", skills=frozenset(), availability=availability, max_work_minutes=0),
        ),
        jobs=(
            Job(id="a", required_skills=frozenset({"a"}), start_minute=0, end_minute=4),
            Job(
                id="b", required_skills=frozenset({"b"}), start_minute=4, end_minute=10, priority=2
            ),
        ),
    )
    config = QUBOConfig(
        objective=ObjectiveWeights(unassigned_penalty=100, cost_weight=3, imbalance_weight=2)
    )
    model = encode_qubo(instance, config)
    assert model.mapping.load_unit == 2
    assert (
        next(group for group in model.mapping.integers if group.role == "load_min").upper_bound == 0
    )
    result = verify_equivalence(instance, model=model)
    assert result.status == "PASS"
    assert result.classical_objective == 57  # 3*(ceil(61*4/60)+ceil(97*6/60)) + 2*(6-0).
    assert result.canonical_encoding_checked_count == 4


def test_best_valid_energy_is_reported_for_safe_cost_five_mini_case() -> None:
    instance = SchedulingInstance(
        instance_id="safe-cost-five",
        seed=0,
        workers=(
            Worker(
                id="w",
                skills=frozenset({"a"}),
                availability=(TimeWindow(start_minute=0, end_minute=1),),
                max_work_minutes=1,
                cost_per_hour=300,
            ),
        ),
        jobs=(Job(id="j", required_skills=frozenset({"a"}), start_minute=0, end_minute=1),),
    )
    config = QUBOConfig(objective=ObjectiveWeights(unassigned_penalty=17, cost_weight=1))
    result = solve_qubo(encode_qubo(instance, config), instance)
    assert result.best_valid_energy == result.ground_energy == 5
    data = result.model_dump(mode="json")
    data["best_valid_energy"] = None
    with pytest.raises(ValidationError, match="best valid energy"):
        QUBOSolverResult.model_validate(data)
