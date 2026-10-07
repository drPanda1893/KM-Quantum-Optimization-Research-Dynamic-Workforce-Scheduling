"""A second tiny oracle written without production validator/objective helpers.

The frozen 150-seed development range probes all three objective components,
skill subsets, separate availability periods, mandatory jobs, capacity limits,
and empty dimensions. It is a correctness audit, not a performance benchmark.
"""

import random
from itertools import product

from km_quantum.domain import (
    Job,
    ObjectiveWeights,
    SchedulingInstance,
    SolverConfig,
    TimeWindow,
    Worker,
)
from km_quantum.solvers import solve_cp_sat

AUDIT_SEEDS = range(150)


def _audit_problem(seed: int) -> tuple[SchedulingInstance, ObjectiveWeights]:
    rng = random.Random(seed + 1200)
    workers = []
    for index in range(rng.randrange(4)):
        windows = ((0, 60),) if rng.random() < 0.5 else ((0, 20), (30, 60))
        workers.append(
            Worker(
                id=f"w{index}",
                skills=frozenset(skill for skill in ("a", "b") if rng.random() < 0.5),
                availability=tuple(
                    TimeWindow(start_minute=start, end_minute=end) for start, end in windows
                ),
                max_work_minutes=rng.randrange(61),
                cost_per_hour=rng.choice((None, 1, 17, 700)),
            )
        )
    jobs = []
    for index in range(rng.randrange(6)):
        start = rng.randrange(50)
        jobs.append(
            Job(
                id=f"j{index}",
                required_skills=frozenset(skill for skill in ("a", "b") if rng.random() < 0.5),
                start_minute=start,
                end_minute=start + rng.randrange(1, 11),
                priority=rng.randrange(1, 5),
                unassigned_penalty=rng.choice((None, 0, 3, 9)),
                must_assign=rng.random() < 0.25,
            )
        )
    weights = ObjectiveWeights(
        unassigned_penalty=rng.randrange(12),
        cost_weight=rng.randrange(4),
        imbalance_weight=rng.randrange(4),
    )
    instance = SchedulingInstance(
        instance_id=f"independent-{seed}",
        seed=seed,
        workers=tuple(workers),
        jobs=tuple(jobs),
    )
    return instance, weights


def _plain_python_optimum(instance: SchedulingInstance, weights: ObjectiveWeights) -> int | None:
    """Enumerate all choices and calculate feasibility/cost directly from fields."""
    best: int | None = None
    for choices in product(range(-1, len(instance.workers)), repeat=len(instance.jobs)):
        loads = [0] * len(instance.workers)
        selected: list[list[Job]] = [[] for _ in instance.workers]
        value = 0
        valid = True
        for job, worker_index in zip(instance.jobs, choices, strict=True):
            if worker_index == -1:
                if job.must_assign:
                    valid = False
                    break
                base = (
                    weights.unassigned_penalty
                    if job.unassigned_penalty is None
                    else job.unassigned_penalty
                )
                value += job.priority * base
                continue
            worker = instance.workers[worker_index]
            if not job.required_skills.issubset(worker.skills) or not any(
                job.start_minute >= window.start_minute and job.end_minute <= window.end_minute
                for window in worker.availability
            ):
                valid = False
                break
            duration = job.end_minute - job.start_minute
            loads[worker_index] += duration
            if loads[worker_index] > worker.max_work_minutes or any(
                max(job.start_minute, other.start_minute) < min(job.end_minute, other.end_minute)
                for other in selected[worker_index]
            ):
                valid = False
                break
            selected[worker_index].append(job)
            rate = worker.cost_per_hour or 0
            value += weights.cost_weight * ((rate * duration + 59) // 60)
        if valid:
            value += weights.imbalance_weight * (max(loads) - min(loads) if loads else 0)
            best = value if best is None else min(best, value)
    return best


def test_cp_sat_matches_fully_independent_plain_python_oracle() -> None:
    for seed in AUDIT_SEEDS:
        instance, weights = _audit_problem(seed)
        expected = _plain_python_optimum(instance, weights)
        result = solve_cp_sat(instance, SolverConfig(objective=weights))
        assert result.objective_value == expected, f"Objective disagreement on audit seed {seed}"
        assert result.status == ("OPTIMAL" if expected is not None else "INFEASIBLE"), seed
        assert result.proven_optimal == (expected is not None), seed
