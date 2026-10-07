"""Ten tiny fixed-slot cases with optima derivable without a solver.

``metadata.reference`` records a reference objective configuration, expected
proof status and objective, one optimal assignment, and the manual argument.
The default priority-weighted unassigned penalty is 100000; worker cost and
workload imbalance are disabled except where explicitly configured. ``None``
in a reference assignment means intentionally unassigned. Infeasible cases
have no reference assignment or objective. No solver output builds this data.
"""

from __future__ import annotations

from typing import Any

from km_quantum.domain import Job, SchedulingInstance, TimeWindow, Worker


def _worker(
    worker_id: str,
    skills: tuple[str, ...] = ("a",),
    windows: tuple[tuple[int, int], ...] = ((0, 180),),
    capacity: int = 180,
    cost: int | None = None,
) -> Worker:
    return Worker(
        id=worker_id,
        skills=frozenset(skills),
        availability=tuple(
            TimeWindow(start_minute=start, end_minute=end) for start, end in windows
        ),
        max_work_minutes=capacity,
        cost_per_hour=cost,
    )


def _job(
    job_id: str,
    start: int = 0,
    end: int = 60,
    skill: str = "a",
    priority: int = 1,
    mandatory: bool = False,
) -> Job:
    return Job(
        id=job_id,
        required_skills=frozenset((skill,)),
        start_minute=start,
        end_minute=end,
        priority=priority,
        must_assign=mandatory,
    )


def _case(
    name: str,
    workers: tuple[Worker, ...],
    jobs: tuple[Job, ...],
    objective: int | None,
    assignment: dict[str, str | None] | None,
    explanation: str,
    *,
    cost_weight: int = 0,
    optimum_count: int = 1,
) -> SchedulingInstance:
    reference: dict[str, Any] = {
        "objective_config": {"cost_weight": cost_weight},
        "expected_status": "INFEASIBLE" if objective is None else "OPTIMAL",
        "expected_objective": objective,
        "one_optimal_assignment": assignment,
        "optimum_count": optimum_count if objective is not None else 0,
        "explanation": explanation,
    }
    return SchedulingInstance(
        instance_id=f"hand-{name}",
        seed=0,
        workers=workers,
        jobs=jobs,
        generator_parameters=None,
        metadata={"source": "hand-verifiable", "reference": reference},
    )


def hand_cases() -> dict[str, SchedulingInstance]:
    """Return fresh immutable domain objects for ten independently specified cases."""
    return {
        "unique_skills": _case(
            "unique_skills",
            (_worker("worker-a"), _worker("worker-b", ("b",))),
            (_job("job-a"), _job("job-b", skill="b")),
            0,
            {"job-a": "worker-a", "job-b": "worker-b"},
            "Each job has exactly one skilled worker; both can run concurrently. Unique optimum.",
        ),
        "touching_intervals": _case(
            "touching_intervals",
            (_worker("worker-a", capacity=60),),
            (_job("job-a", 0, 30), _job("job-b", 30, 60)),
            0,
            {"job-a": "worker-a", "job-b": "worker-a"},
            "Half-open intervals touch at minute 30 without overlap; total workload is exactly 60.",
        ),
        "priority_overlap": _case(
            "priority_overlap",
            (_worker("worker-a"),),
            (_job("job-low"), _job("job-high", priority=3)),
            100000,
            {"job-low": None, "job-high": "worker-a"},
            "One of the overlapping jobs must be omitted. "
            "Omitting priority 1 costs 100000, versus 300000.",
        ),
        "workload_limit": _case(
            "workload_limit",
            (_worker("worker-a", capacity=60),),
            (_job("job-low", 0, 60), _job("job-high", 60, 120, priority=2)),
            100000,
            {"job-low": None, "job-high": "worker-a"},
            "The jobs do not overlap, but 120 minutes exceed the 60-minute cap. Keep priority 2.",
        ),
        "no_compatible_worker": _case(
            "no_compatible_worker",
            (_worker("worker-a"),),
            (_job("job-b", skill="b", priority=2),),
            200000,
            {"job-b": None},
            "The only worker lacks skill b. The optional priority-2 job incurs 200000.",
        ),
        "unavailable_job": _case(
            "unavailable_job",
            (_worker("worker-a", windows=((0, 30),)),),
            (_job("job-a", 30, 60),),
            100000,
            {"job-a": None},
            "The job begins at the end of the worker's availability and cannot be assigned.",
        ),
        "mandatory_infeasible": _case(
            "mandatory_infeasible",
            (_worker("worker-a"),),
            (_job("job-b", skill="b", mandatory=True),),
            None,
            None,
            "A mandatory job has no skilled worker, so no feasible schedule exists.",
        ),
        "multiple_optima": _case(
            "multiple_optima",
            (_worker("worker-a"), _worker("worker-b")),
            (_job("job-a"),),
            0,
            {"job-a": "worker-a"},
            "Either identical worker can execute the one job at zero objective. "
            "Exactly two optima.",
            optimum_count=2,
        ),
        "cost_choice": _case(
            "cost_choice",
            (_worker("worker-expensive", cost=6000), _worker("worker-cheap", cost=3000)),
            (_job("job-a", 0, 30),),
            1500,
            {"job-a": "worker-cheap"},
            "With cost weight 1, 30 minutes cost 1500 or 3000. "
            "Both are below the 100000 omission penalty.",
            cost_weight=1,
        ),
        "split_availability": _case(
            "split_availability",
            (_worker("worker-a", windows=((0, 30), (60, 90)), capacity=60),),
            (_job("job-inside", 0, 30), _job("job-crossing", 20, 70, priority=3)),
            300000,
            {"job-inside": "worker-a", "job-crossing": None},
            "The inside job fits one availability interval; "
            "the crossing job spans the unavailable break.",
        ),
    }
