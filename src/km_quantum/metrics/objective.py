"""Solver-independent, exact integer objective with auditable components."""

from pydantic import Field

from km_quantum.domain import (
    FrozenModel,
    Job,
    ObjectiveWeights,
    Schedule,
    SchedulingInstance,
    Worker,
)

from .validation import validate_schedule


class ObjectiveBreakdown(FrozenModel):
    total: int = Field(ge=0)
    unassigned: int = Field(ge=0)
    cost: int = Field(ge=0)
    imbalance: int = Field(ge=0)
    worker_loads: dict[str, int]


def unassigned_cost(job: Job, weights: ObjectiveWeights) -> int:
    base = weights.unassigned_penalty if job.unassigned_penalty is None else job.unassigned_penalty
    return base * job.priority


def assignment_cost(worker: Worker, job: Job, weights: ObjectiveWeights) -> int:
    # Round EACH assignment UP to the next minor monetary unit, using integers.
    rate = 0 if worker.cost_per_hour is None else worker.cost_per_hour
    return weights.cost_weight * ((rate * job.duration_minutes + 59) // 60)


def evaluate_objective(
    instance: SchedulingInstance,
    schedule: Schedule,
    weights: ObjectiveWeights | None = None,
) -> ObjectiveBreakdown:
    """Score feasible schedules; ignore any objective claimed by their producer."""
    validation = validate_schedule(instance, schedule)
    if not validation.is_valid:
        codes = ", ".join(violation.code for violation in validation.violations)
        raise ValueError(f"cannot evaluate an invalid schedule: {codes}")
    schedule = Schedule.model_validate(schedule.model_dump(warnings=False))
    weights = weights or ObjectiveWeights()
    workers = {worker.id: worker for worker in instance.workers}
    jobs = {job.id: job for job in instance.jobs}
    loads = dict.fromkeys(workers, 0)
    cost = 0
    for assignment in schedule.assignments:
        worker, job = workers[assignment.worker_id], jobs[assignment.job_id]
        loads[worker.id] += job.duration_minutes
        cost += assignment_cost(worker, job, weights)
    unassigned = sum(
        unassigned_cost(jobs[job_id], weights) for job_id in schedule.unassigned_job_ids
    )
    imbalance = weights.imbalance_weight * (
        max(loads.values()) - min(loads.values()) if loads else 0
    )
    return ObjectiveBreakdown(
        total=unassigned + cost + imbalance,
        unassigned=unassigned,
        cost=cost,
        imbalance=imbalance,
        worker_loads=loads,
    )
