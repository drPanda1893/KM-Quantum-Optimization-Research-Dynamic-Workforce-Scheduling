"""Agreement with an independent finite oracle, including every objective term."""

import random
from collections.abc import Callable

import pytest

from km_quantum.domain import (
    Job,
    ObjectiveWeights,
    SchedulingInstance,
    SolverConfig,
    SolverResult,
    TimeWindow,
    Worker,
)
from km_quantum.instances import hand_cases
from km_quantum.metrics import evaluate_objective, validate_schedule
from km_quantum.solvers import solve_cp_sat, solve_exhaustive
from km_quantum.solvers.classical import exhaustive as exhaustive_module

Solver = Callable[[SchedulingInstance, SolverConfig | None], SolverResult]
SOLVERS = [solve_cp_sat, solve_exhaustive]


def worker(
    identifier: str = "worker",
    *,
    skills: frozenset[str] = frozenset({"skill"}),
    max_work_minutes: int = 120,
    cost_per_hour: int | None = None,
) -> Worker:
    return Worker(
        id=identifier,
        skills=skills,
        availability=(TimeWindow(start_minute=0, end_minute=120),),
        max_work_minutes=max_work_minutes,
        cost_per_hour=cost_per_hour,
    )


def job(identifier: str = "job", start: int = 0, end: int = 60, **kwargs: object) -> Job:
    return Job(
        id=identifier,
        required_skills=frozenset({"skill"}),
        start_minute=start,
        end_minute=end,
        **kwargs,
    )


def instance(workers: tuple[Worker, ...], jobs: tuple[Job, ...]) -> SchedulingInstance:
    return SchedulingInstance(instance_id="test", seed=0, workers=workers, jobs=jobs)


def assert_verified(result: SolverResult, data: SchedulingInstance, config: SolverConfig) -> None:
    assert result.schedule is not None
    assert validate_schedule(data, result.schedule).is_valid
    assert (
        evaluate_objective(data, result.schedule, config.objective).total == result.objective_value
    )
    assert result.schedule.objective_value == result.objective_value
    assert result.runtime_ms >= 0
    assert result.solver_metadata["config"] == config.model_dump(mode="json")


@pytest.mark.parametrize("case_name", list(hand_cases()))
def test_both_solvers_match_hand_verified_reference_cases(case_name: str) -> None:
    data = hand_cases()[case_name]
    reference = data.metadata["reference"]
    assert isinstance(reference, dict)
    objective_config = reference["objective_config"]
    assert isinstance(objective_config, dict)
    config = SolverConfig(objective=ObjectiveWeights(**objective_config))
    for solver in SOLVERS:
        result = solver(data, config)
        assert result.status == reference["expected_status"]
        assert result.objective_value == reference["expected_objective"]
        if result.schedule is not None:
            assert_verified(result, data, config)
            assert result.proven_optimal
            assert result.optimality_gap == 0
            if reference["optimum_count"] == 1:
                actual = {a.job_id: a.worker_id for a in result.schedule.assignments}
                actual.update(dict.fromkeys(result.schedule.unassigned_job_ids))
                assert actual == reference["one_optimal_assignment"]
        else:
            assert not result.proven_optimal


@pytest.mark.parametrize("solver", SOLVERS, ids=["cp_sat", "exhaustive"])
def test_single_compatible_job(solver: Solver) -> None:
    data = instance((worker(),), (job(),))
    config = SolverConfig()
    result = solver(data, config)
    assert_verified(result, data, config)
    assert result.status == "OPTIMAL"
    assert result.proven_optimal
    assert result.objective_value == result.best_bound == result.optimality_gap == 0
    assert result.schedule is not None
    assert [(a.worker_id, a.job_id) for a in result.schedule.assignments] == [("worker", "job")]


@pytest.mark.parametrize("solver", SOLVERS, ids=["cp_sat", "exhaustive"])
def test_required_overlapping_jobs_are_infeasible(solver: Solver) -> None:
    data = instance((worker(),), (job("first", must_assign=True), job("second", must_assign=True)))
    result = solver(data, None)
    assert result.status == "INFEASIBLE"
    assert result.schedule is None
    assert result.objective_value is None
    assert result.best_bound is None
    assert result.optimality_gap is None
    assert not result.proven_optimal


@pytest.mark.parametrize("solver", SOLVERS, ids=["cp_sat", "exhaustive"])
def test_priority_and_individual_penalty_override(solver: Solver) -> None:
    data = instance(
        (worker(),),
        (job("low", priority=3, unassigned_penalty=7), job("high", priority=2)),
    )
    config = SolverConfig(objective=ObjectiveWeights(unassigned_penalty=11))
    result = solver(data, config)
    assert_verified(result, data, config)
    assert result.objective_value == 21
    assert result.schedule is not None
    assert result.schedule.unassigned_job_ids == ("low",)


@pytest.mark.parametrize("solver", SOLVERS, ids=["cp_sat", "exhaustive"])
def test_cost_and_imbalance_change_optimum(solver: Solver) -> None:
    data = instance(
        (worker("cheap", cost_per_hour=60), worker("expensive", cost_per_hour=1200)),
        (job("first", 0, 60, must_assign=True), job("second", 60, 120, must_assign=True)),
    )
    config = SolverConfig(objective=ObjectiveWeights(cost_weight=1, imbalance_weight=10))
    result = solver(data, config)
    assert_verified(result, data, config)
    assert result.objective_value == 1260
    assert result.schedule is not None
    breakdown = evaluate_objective(data, result.schedule, config.objective)
    assert breakdown.cost == 1260
    assert breakdown.imbalance == 0
    assert breakdown.worker_loads == {"cheap": 60, "expensive": 60}
    cost_only = solver(data, SolverConfig(objective=ObjectiveWeights(cost_weight=1)))
    assert cost_only.objective_value == 120


@pytest.mark.parametrize("solver", SOLVERS, ids=["cp_sat", "exhaustive"])
def test_cost_rounding_is_per_assignment(solver: Solver) -> None:
    data = instance(
        (worker(cost_per_hour=1),),
        (job("first", 0, 1, must_assign=True), job("second", 1, 2, must_assign=True)),
    )
    config = SolverConfig(objective=ObjectiveWeights(cost_weight=1, imbalance_weight=100))
    result = solver(data, config)
    assert_verified(result, data, config)
    assert result.objective_value == 2  # ceil(1/60) + ceil(1/60), zero one-worker imbalance.


@pytest.mark.parametrize("solver", SOLVERS, ids=["cp_sat", "exhaustive"])
def test_adjacent_availability_windows_do_not_allow_crossing_job(solver: Solver) -> None:
    available = worker().model_copy(
        update={
            "availability": (
                TimeWindow(start_minute=0, end_minute=30),
                TimeWindow(start_minute=30, end_minute=60),
            )
        }
    )
    data = instance(
        (available,),
        (job("before", 0, 30), job("after", 30, 60), job("crossing", 15, 45, priority=3)),
    )
    config = SolverConfig()
    result = solver(data, config)
    assert_verified(result, data, config)
    assert result.objective_value == 300000
    assert result.schedule is not None
    assert result.schedule.unassigned_job_ids == ("crossing",)
    assert {a.job_id for a in result.schedule.assignments} == {"before", "after"}


@pytest.mark.parametrize("solver", SOLVERS, ids=["cp_sat", "exhaustive"])
def test_no_workers_still_has_explicit_unassigned_coverage(solver: Solver) -> None:
    data = instance((), (job(priority=3),))
    config = SolverConfig(objective=ObjectiveWeights(unassigned_penalty=17, imbalance_weight=5))
    result = solver(data, config)
    assert_verified(result, data, config)
    assert result.objective_value == 51
    assert result.schedule is not None
    assert result.schedule.unassigned_job_ids == ("job",)
    required = instance((), (job(must_assign=True),))
    assert solver(required, config).status == "INFEASIBLE"


@pytest.mark.parametrize("solver", SOLVERS, ids=["cp_sat", "exhaustive"])
@pytest.mark.parametrize("workers", [(), (worker(),)])
def test_empty_jobs_prove_zero_optimum(solver: Solver, workers: tuple[Worker, ...]) -> None:
    data = instance(workers, ())
    config = SolverConfig(objective=ObjectiveWeights(cost_weight=3, imbalance_weight=17))
    result = solver(data, config)
    assert_verified(result, data, config)
    assert result.status == "OPTIMAL"
    assert result.proven_optimal
    assert result.objective_value == result.best_bound == result.optimality_gap == 0
    assert result.schedule is not None
    assert result.schedule.assignments == result.schedule.unassigned_job_ids == ()


def tiny_random_instance(seed: int) -> SchedulingInstance:
    """Independently create tiny cases with missing skills, gaps, overlap and tight capacity."""
    rng = random.Random(seed)
    skills = ("a", "b", "c")
    workers = tuple(
        Worker(
            id=f"w{i}",
            skills=frozenset(skill for skill in skills if rng.random() < 0.6),
            availability=(TimeWindow(start_minute=0, end_minute=70),)
            if rng.random() < 0.5
            else (
                TimeWindow(start_minute=0, end_minute=25),
                TimeWindow(start_minute=30, end_minute=70),
            ),
            max_work_minutes=rng.choice((0, 20, 40, 70)),
            cost_per_hour=rng.choice((None, 1, 67, 1200)),
        )
        for i in range(3)
    )
    jobs = []
    for i in range(5):
        start = rng.randrange(6) * 10
        jobs.append(
            Job(
                id=f"j{i}",
                required_skills=frozenset(skill for skill in skills if rng.random() < 0.35),
                start_minute=start,
                end_minute=start + rng.choice((10, 20)),
                priority=rng.randrange(1, 5),
                unassigned_penalty=rng.choice((None, 0, 77)),
                must_assign=rng.random() < 0.2,
            )
        )
    return SchedulingInstance(
        instance_id=f"random-{seed}", seed=seed, workers=workers, jobs=tuple(jobs)
    )


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize(
    "weights",
    [
        ObjectiveWeights(),
        ObjectiveWeights(unassigned_penalty=17, cost_weight=3, imbalance_weight=2),
        ObjectiveWeights(unassigned_penalty=0),
    ],
    ids=["coverage", "mixed", "zero-default-penalty"],
)
def test_cp_sat_matches_independent_exhaustive_oracle(seed: int, weights: ObjectiveWeights) -> None:
    data = tiny_random_instance(seed)
    config = SolverConfig(objective=weights)
    cp = solve_cp_sat(data, config)
    exhaustive = solve_exhaustive(data, config)
    assert cp.status == exhaustive.status
    assert cp.objective_value == exhaustive.objective_value
    assert cp.proven_optimal == exhaustive.proven_optimal
    assert exhaustive.solver_metadata["state_count"] == 4**5
    assert exhaustive.solver_metadata["explored_states"] == 4**5
    if cp.schedule is not None:
        assert_verified(cp, data, config)
        assert_verified(exhaustive, data, config)
        assert cp.optimality_gap == exhaustive.optimality_gap == 0


def test_deterministic_cp_configuration_and_metadata() -> None:
    data = instance((worker("a"), worker("b")), (job("first", 0, 60), job("second", 60, 120)))
    config = SolverConfig(random_seed=41, num_search_workers=1, max_deterministic_time=0.5)
    first, second = solve_cp_sat(data, config), solve_cp_sat(data, config)
    assert first.status == second.status == "OPTIMAL"
    assert first.objective_value == second.objective_value
    assert first.schedule is not None and second.schedule is not None
    assert first.schedule.assignments == second.schedule.assignments
    assert first.schedule.unassigned_job_ids == second.schedule.unassigned_job_ids
    assert first.solver_metadata["config"] == config.model_dump(mode="json")
    assert first.solver_metadata["solver"] == "cp_sat"
    assert first.solver_metadata["solver_version"]
    assert first.solver_metadata["search_wall_time_ms"] >= 0
    assert first.solver_metadata["deterministic_time"] >= 0


def test_exhaustive_state_cap_checks_full_space_before_search() -> None:
    # Both workers lack the required skill. A compatibility-pruned enumeration
    # would incorrectly avoid this cap: this oracle enumerates all nine states.
    data = instance(
        (worker("a", skills=frozenset()), worker("b", skills=frozenset())), (job("a"), job("b"))
    )
    with pytest.raises(ValueError, match="exhaustive_max_states"):
        solve_exhaustive(data, SolverConfig(exhaustive_max_states=8))
    result = solve_exhaustive(data, SolverConfig(exhaustive_max_states=9))
    assert result.status == "OPTIMAL"
    assert result.solver_metadata["explored_states"] == 9
    assert result.solver_metadata["feasible_states"] == 1


def test_exhaustive_interrupted_incumbent_is_only_feasible(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter((0.0, 0.1, 0.2, 0.3))
    monkeypatch.setattr(exhaustive_module, "perf_counter", lambda: next(ticks))
    data = instance((worker(),), (job(),))
    result = solve_exhaustive(data, SolverConfig(time_limit_seconds=0.15))
    assert result.status == "FEASIBLE"
    assert not result.proven_optimal
    assert result.objective_value == 100000
    assert result.best_bound == 0
    assert result.optimality_gap == 1
    assert result.schedule is not None
    assert result.schedule.unassigned_job_ids == ("job",)
    assert result.solver_metadata["enumeration_complete"] is False
    assert result.solver_metadata["explored_states"] == 1


@pytest.mark.parametrize("solver", SOLVERS, ids=["cp_sat", "exhaustive"])
def test_tiny_time_limit_does_not_claim_optimality(solver: Solver) -> None:
    result = solver(tiny_random_instance(4), SolverConfig(time_limit_seconds=1e-12))
    assert result.status == "UNKNOWN"
    assert result.schedule is None
    assert result.objective_value is None
    assert result.optimality_gap is None
    assert not result.proven_optimal


@pytest.mark.parametrize("penalty", [2**53, 2**64, 10**400])
def test_cp_sat_rejects_objective_unsafe_for_exact_exported_bounds(penalty: int) -> None:
    data = instance((worker(),), (job(unassigned_penalty=penalty),))
    with pytest.raises(ValueError, match="Objective coefficients"):
        solve_cp_sat(data)


def test_cp_sat_checks_cost_range_before_constructing_expression() -> None:
    data = instance((worker(cost_per_hour=10**400),), (job(),))
    with pytest.raises(ValueError, match="Objective coefficients"):
        solve_cp_sat(data, SolverConfig(objective=ObjectiveWeights(cost_weight=1)))


def test_cp_sat_checks_imbalance_range_before_constructing_expression() -> None:
    data = instance((worker("a"), worker("b")), (job(),))
    with pytest.raises(ValueError, match="Imbalance weight"):
        solve_cp_sat(data, SolverConfig(objective=ObjectiveWeights(imbalance_weight=10**400)))


def test_exhaustive_preserves_large_exact_objective_without_inexact_float_bound() -> None:
    data = instance((), (job(unassigned_penalty=10**400),))
    result = solve_exhaustive(data)
    assert result.status == "OPTIMAL"
    assert result.proven_optimal
    assert result.objective_value == 10**400
    assert result.best_bound is None
    assert result.optimality_gap == 0
    assert result.solver_metadata["bound_omitted_reason"]


def test_cp_sat_rejects_aggregate_objective_overflow() -> None:
    data = instance(
        (worker(),), (job("a", unassigned_penalty=2**52), job("b", unassigned_penalty=2**52))
    )
    with pytest.raises(ValueError, match="Expanded objective upper bound"):
        solve_cp_sat(data)
