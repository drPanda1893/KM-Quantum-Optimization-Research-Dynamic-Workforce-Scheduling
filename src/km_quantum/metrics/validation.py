"""Independent schedule validation. No solver dependencies are used here."""

from collections import Counter, defaultdict

from pydantic import ValidationError, computed_field

from km_quantum.domain import FrozenModel, Job, Schedule, SchedulingInstance


class Violation(FrozenModel):
    code: str
    message: str
    job_id: str | None = None
    worker_id: str | None = None


class ValidationResult(FrozenModel):
    is_valid: bool
    violations: tuple[Violation, ...]

    @computed_field  # type: ignore[prop-decorator]  # Pydantic supports this decorator order.
    @property
    def violation_count(self) -> int:
        return len(self.violations)


def validate_schedule(instance: SchedulingInstance, schedule: object) -> ValidationResult:
    """Check structure, complete partition, known IDs and every hard constraint.

    Raw JSON objects are accepted so malformed candidates produce structured
    violations instead of escaping through a schema exception.
    """
    violations: list[Violation] = []

    def add(code: str, message: str, job: str | None = None, worker: str | None = None) -> None:
        violations.append(Violation(code=code, message=message, job_id=job, worker_id=worker))

    try:
        # Reparse even model instances: do not trust model_construct / model_copy.
        raw = schedule.model_dump(warnings=False) if isinstance(schedule, Schedule) else schedule
        candidate = Schedule.model_validate(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        add("invalid_structure", str(exc))
        return ValidationResult(is_valid=False, violations=tuple(violations))

    if candidate.instance_id != instance.instance_id:
        add("instance_mismatch", "schedule.instance_id does not match the instance")
    workers = {worker.id: worker for worker in instance.workers}
    jobs = {job.id: job for job in instance.jobs}
    assigned = Counter(assignment.job_id for assignment in candidate.assignments)
    unassigned = Counter(candidate.unassigned_job_ids)
    by_worker: dict[str, list[Job]] = defaultdict(list)

    for job_id, count in assigned.items():
        if count > 1:
            add("duplicate_job", "job is assigned more than once", job_id)
    for job_id, count in unassigned.items():
        if count > 1:
            add("duplicate_unassigned", "job appears more than once as unassigned", job_id)
        if job_id not in jobs:
            add("unknown_job", "unassigned job ID is unknown", job_id)
        if job_id in assigned:
            add("assigned_and_unassigned", "job is both assigned and unassigned", job_id)
    for input_job in instance.jobs:
        if input_job.id not in assigned and input_job.id not in unassigned:
            add("missing_job", "job is absent from the schedule", input_job.id)
        if input_job.must_assign and input_job.id not in assigned:
            add("required_job_unassigned", "mandatory job has no assignment", input_job.id)

    for assignment in candidate.assignments:
        job = jobs.get(assignment.job_id)
        worker = workers.get(assignment.worker_id)
        if job is None:
            add(
                "unknown_job", "assigned job ID is unknown", assignment.job_id, assignment.worker_id
            )
        if worker is None:
            add("unknown_worker", "worker ID is unknown", assignment.job_id, assignment.worker_id)
        if job is None or worker is None:
            continue
        by_worker[worker.id].append(job)
        if not job.required_skills.issubset(worker.skills):
            add("skill_mismatch", "worker lacks required skills", job.id, worker.id)
        if not any(
            period.start_minute <= job.start_minute and job.end_minute <= period.end_minute
            for period in worker.availability
        ):
            add("unavailable", "job is not fully inside an availability period", job.id, worker.id)

    for worker_id, worker_jobs in by_worker.items():
        load = sum(job.duration_minutes for job in worker_jobs)
        if load > workers[worker_id].max_work_minutes:
            add("workload_exceeded", "assigned duration exceeds max_work_minutes", worker=worker_id)
        ordered = sorted(worker_jobs, key=lambda job: (job.start_minute, job.end_minute, job.id))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if right.start_minute >= left.end_minute:
                    break
                add("overlap", f"jobs {left.id} and {right.id} overlap", right.id, worker_id)

    return ValidationResult(is_valid=not violations, violations=tuple(violations))
