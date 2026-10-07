"""Schema and independent oracle tests, including intentionally malformed candidates."""

import json

import pytest
from pydantic import ValidationError

from km_quantum.domain import (
    Assignment,
    Job,
    ObjectiveWeights,
    Schedule,
    SchedulingInstance,
    SolverConfig,
    SolverResult,
    TimeWindow,
    Worker,
)
from km_quantum.metrics import assignment_cost, evaluate_objective, validate_schedule


def worker(**updates):
    fields = dict(
        id="w1",
        skills={"a"},
        availability=[TimeWindow(start_minute=0, end_minute=120)],
        max_work_minutes=120,
    )
    fields.update(updates)
    return Worker(**fields)


def job(**updates):
    fields = dict(id="j1", required_skills={"a"}, start_minute=0, end_minute=60)
    fields.update(updates)
    return Job(**fields)


def instance(workers=None, jobs=None):
    return SchedulingInstance(
        instance_id="test",
        seed=0,
        workers=[worker()] if workers is None else workers,
        jobs=[job()] if jobs is None else jobs,
    )


def schedule(assignments=None, unassigned=(), **updates):
    fields = dict(
        instance_id="test",
        assignments=[Assignment(worker_id="w1", job_id="j1")]
        if assignments is None
        else assignments,
        unassigned_job_ids=unassigned,
    )
    fields.update(updates)
    return Schedule(**fields)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": " "},
        {"priority": 0},
        {"priority": True},
        {"start_minute": -1},
        {"end_minute": 0},
        {"end_minute": 2.5},
        {"unassigned_penalty": -1},
        {"required_skills": {""}},
        {"must_assign": "yes"},
        {"unrecognized": 1},
    ],
)
def test_invalid_job_schema(changes):
    with pytest.raises(ValidationError):
        job(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": ""},
        {"max_work_minutes": -1},
        {"cost_per_hour": -1},
        {"max_work_minutes": 30.5},
        {"skills": {""}},
        {
            "availability": [
                TimeWindow(start_minute=0, end_minute=60),
                TimeWindow(start_minute=30, end_minute=90),
            ]
        },
    ],
)
def test_invalid_worker_schema(changes):
    with pytest.raises(ValidationError):
        worker(**changes)


@pytest.mark.parametrize("start,end", [(0, 0), (1, 0), (-1, 30), (True, 30)])
def test_invalid_time_window(start, end):
    with pytest.raises(ValidationError):
        TimeWindow(start_minute=start, end_minute=end)


@pytest.mark.parametrize("kind", ["workers", "jobs"])
def test_duplicate_input_ids_rejected(kind):
    with pytest.raises(ValidationError):
        instance(**{kind: [worker(), worker()] if kind == "workers" else [job(), job()]})


@pytest.mark.parametrize(
    "changes",
    [
        {"time_limit_seconds": 0},
        {"time_limit_seconds": float("inf")},
        {"num_search_workers": 0},
        {"num_search_workers": 2**31},
        {"random_seed": -1},
        {"random_seed": 2**31},
        {"max_deterministic_time": 0},
        {"exhaustive_max_states": 0},
    ],
)
def test_invalid_solver_config(changes):
    with pytest.raises(ValidationError):
        SolverConfig(**changes)


def test_mathematical_fields_are_frozen_and_roundtrip_is_canonical():
    original = instance(workers=[worker(skills={"z", "a"})])
    with pytest.raises(ValidationError):
        original.workers[0].max_work_minutes = 0
    serialized = original.model_dump_json()
    assert json.loads(serialized)["workers"][0]["skills"] == ["a", "z"]
    assert SchedulingInstance.model_validate_json(serialized) == original
    assert Schedule.model_validate_json(schedule().model_dump_json()) == schedule()


@pytest.mark.parametrize(
    "candidate,code",
    [
        (schedule(assignments=[Assignment(worker_id="w1", job_id="j1")] * 2), "duplicate_job"),
        (schedule(unassigned=["j1"]), "assigned_and_unassigned"),
        (schedule(assignments=[], unassigned=["j1", "j1"]), "duplicate_unassigned"),
        (schedule(assignments=[]), "missing_job"),
        (schedule(assignments=[], unassigned=["other"]), "unknown_job"),
        (schedule(assignments=[Assignment(worker_id="other", job_id="j1")]), "unknown_worker"),
        (schedule(assignments=[Assignment(worker_id="w1", job_id="other")]), "unknown_job"),
        (schedule(instance_id="other"), "instance_mismatch"),
        ({"instance_id": "test", "assignments": [{"job_id": "j1"}]}, "invalid_structure"),
        ({"instance_id": "test", "assignments": "bad"}, "invalid_structure"),
        (None, "invalid_structure"),
    ],
)
def test_structured_validator_reports_invalid_candidates(candidate, code):
    result = validate_schedule(instance(), candidate)
    assert not result.is_valid
    assert code in {violation.code for violation in result.violations}
    assert result.violation_count == len(result.violations)
    assert result.model_dump()["violation_count"] == result.violation_count


def test_schema_bypass_is_revalidated():
    invalid = Schedule.model_construct(instance_id="test", assignments="broken")
    assert not validate_schedule(instance(), invalid).is_valid


@pytest.mark.parametrize(
    "workers,jobs,code",
    [
        ([worker(skills={"b"})], [job()], "skill_mismatch"),
        ([worker(availability=[])], [job()], "unavailable"),
        (
            [worker(availability=[TimeWindow(start_minute=1, end_minute=120)])],
            [job()],
            "unavailable",
        ),
        ([worker(max_work_minutes=59)], [job()], "workload_exceeded"),
        ([worker()], [job(must_assign=True)], "required_job_unassigned"),
    ],
)
def test_validator_hard_constraints(workers, jobs, code):
    candidate = schedule(assignments=[], unassigned=["j1"]) if jobs[0].must_assign else schedule()
    assert code in {
        v.code for v in validate_schedule(instance(workers, jobs), candidate).violations
    }


def test_multiple_required_skills_are_all_needed():
    candidate = instance(jobs=[job(required_skills={"a", "b"})])
    assert "skill_mismatch" in {v.code for v in validate_schedule(candidate, schedule()).violations}


def test_overlap_and_boundary_semantics():
    assignments = [Assignment(worker_id="w1", job_id="j1"), Assignment(worker_id="w1", job_id="j2")]
    candidate = schedule(assignments=assignments)
    touching = instance(jobs=[job(), job(id="j2", start_minute=60, end_minute=120)])
    assert validate_schedule(touching, candidate).is_valid
    overlapping = instance(jobs=[job(), job(id="j2", start_minute=59, end_minute=119)])
    assert "overlap" in {v.code for v in validate_schedule(overlapping, candidate).violations}


def test_job_cannot_cross_separate_availability_periods():
    periods = [
        TimeWindow(start_minute=0, end_minute=30),
        TimeWindow(start_minute=30, end_minute=120),
    ]
    assert not validate_schedule(
        instance(workers=[worker(availability=periods)]), schedule()
    ).is_valid


def test_empty_instance_and_no_worker_optional_jobs():
    assert validate_schedule(instance([], []), schedule(assignments=[])).is_valid
    assert validate_schedule(
        instance([], [job()]), schedule(assignments=[], unassigned=["j1"])
    ).is_valid


def test_objective_components_and_zero_load_workers():
    problem = instance(
        workers=[worker(cost_per_hour=1200), worker(id="w2")],
        jobs=[job(end_minute=31), job(id="j2", priority=3, unassigned_penalty=7)],
    )
    weights = ObjectiveWeights(unassigned_penalty=100, cost_weight=2, imbalance_weight=4)
    candidate = schedule(unassigned=["j2"], objective_value=999999)
    result = evaluate_objective(problem, candidate, weights)
    assert result.unassigned == 21
    assert result.cost == 1240
    assert result.imbalance == 124
    assert result.total == 1385
    assert result.worker_loads == {"w1": 31, "w2": 0}


@pytest.mark.parametrize("rate,minutes,expected", [(1, 1, 1), (1, 60, 1), (61, 1, 2), (0, 1, 0)])
def test_cost_ceil_is_integer_and_per_assignment(rate, minutes, expected):
    assert (
        assignment_cost(
            worker(cost_per_hour=rate), job(end_minute=minutes), ObjectiveWeights(cost_weight=1)
        )
        == expected
    )


def test_default_objective_and_job_override():
    problem = instance(jobs=[job(priority=4)])
    candidate = schedule(assignments=[], unassigned=["j1"])
    assert evaluate_objective(problem, candidate).total == 400000
    override = instance(jobs=[job(priority=4, unassigned_penalty=0)])
    assert evaluate_objective(override, candidate).total == 0
    assert evaluate_objective(instance([], []), schedule(assignments=[])).total == 0


def test_invalid_schedules_cannot_be_scored():
    with pytest.raises(ValueError, match="invalid schedule"):
        evaluate_objective(instance(), schedule(assignments=[]))


def test_result_cannot_claim_an_unearned_optimum():
    with pytest.raises(ValidationError):
        SolverResult(
            status="INFEASIBLE",
            schedule=None,
            objective_value=None,
            proven_optimal=True,
            runtime_ms=1,
        )
    with pytest.raises(ValidationError):
        SolverResult(
            status="FEASIBLE",
            schedule=schedule(objective_value=0),
            objective_value=0,
            proven_optimal=True,
            runtime_ms=1,
        )
    with pytest.raises(ValidationError):
        SolverResult(
            status="OPTIMAL",
            schedule=schedule(objective_value=1),
            objective_value=0,
            proven_optimal=True,
            runtime_ms=1,
        )


@pytest.mark.parametrize(
    "status,candidate,objective",
    [
        ("OPTIMAL", None, None),
        ("FEASIBLE", None, None),
        ("INFEASIBLE", schedule(objective_value=0), 0),
        ("UNKNOWN", schedule(objective_value=0), 0),
        ("MADE_UP", None, None),
    ],
)
def test_result_status_must_match_incumbent(status, candidate, objective):
    with pytest.raises(ValidationError):
        SolverResult(
            status=status,
            schedule=candidate,
            objective_value=objective,
            proven_optimal=False,
            runtime_ms=1,
        )
