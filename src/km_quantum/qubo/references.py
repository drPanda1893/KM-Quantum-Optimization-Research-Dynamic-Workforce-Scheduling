"""Frozen, synthetic M2 development references for full binary-state audits.

The hand suite A--K exercises the requested individual constraints, costs and
slack, with three additional objective edge cases. Optima in the hand metadata
are derived manually; no solver constructs these cases. Case L deliberately
uses unsafe penalties and is kept outside the successful reference suite.

The random suite freezes seeds 0..99 by default and makes no outcome-based
selection. Most cases use exclusive worker skills and at most four jobs, with
capacities in 0..2 minutes. Consequently each job has at most one assignment
bit, and each workload slack uses at most two bits. Every tenth case exercises
active imbalance with two workers and one one-minute job; the next exercises
shared skills with two workers and at most two one-minute jobs. Those explicit
construction limits control auxiliary growth before any results are examined.
These are correctness/development instances, not a held-out performance set.
"""

from __future__ import annotations

import random
from typing import Any

from km_quantum.domain import Job, ObjectiveWeights, SchedulingInstance, TimeWindow, Worker
from km_quantum.qubo.models import QUBOConfig

RANDOM_SUITE_VERSION = "m2-micro-v1"


def _worker(
    identifier: str,
    skills: tuple[str, ...] = ("a",),
    capacity: int = 4,
    windows: tuple[tuple[int, int], ...] = ((0, 8),),
    cost: int | None = None,
) -> Worker:
    return Worker(
        id=identifier,
        skills=frozenset(skills),
        availability=tuple(
            TimeWindow(start_minute=start, end_minute=end) for start, end in windows
        ),
        max_work_minutes=capacity,
        cost_per_hour=cost,
    )


def _job(
    identifier: str,
    start: int = 0,
    duration: int = 1,
    skills: tuple[str, ...] = ("a",),
    priority: int = 1,
    penalty: int | None = None,
    mandatory: bool = False,
) -> Job:
    return Job(
        id=identifier,
        required_skills=frozenset(skills),
        start_minute=start,
        end_minute=start + duration,
        priority=priority,
        unassigned_penalty=penalty,
        must_assign=mandatory,
    )


def _case(
    name: str,
    workers: tuple[Worker, ...],
    jobs: tuple[Job, ...],
    objective: int | None,
    explanation: str,
    *,
    cost_weight: int = 0,
    imbalance_weight: int = 0,
    penalty_scale: str = "1",
) -> tuple[SchedulingInstance, QUBOConfig]:
    config = QUBOConfig(
        objective=ObjectiveWeights(
            unassigned_penalty=7,
            cost_weight=cost_weight,
            imbalance_weight=imbalance_weight,
        ),
        penalty_scale=penalty_scale,
    )
    reference: dict[str, Any] = {
        "expected_objective": objective,
        "expected_status": "INFEASIBLE" if objective is None else "OPTIMAL",
        "explanation": explanation,
        "qubo_config": config.model_dump(mode="json"),
    }
    instance = SchedulingInstance(
        instance_id=f"m2-{name}",
        seed=0,
        workers=workers,
        jobs=jobs,
        metadata={"source": "m2-hand-verifiable", "reference": reference},
    )
    return instance, config


def m2_hand_cases() -> dict[str, tuple[SchedulingInstance, QUBOConfig]]:
    """Return fourteen feasible cases with safe penalties and manual optima."""
    return {
        "A_single_job": _case(
            "A_single_job",
            (_worker("worker-a"),),
            (_job("job-a", mandatory=True),),
            0,
            "The one mandatory one-minute job fits the only worker; no business cost.",
        ),
        "B_two_workers_cost": _case(
            "B_two_workers_cost",
            (_worker("worker-expensive", cost=120), _worker("worker-cheap", cost=1)),
            (_job("job-a"),),
            1,
            "One minute costs ceil(120/60)=2 or ceil(1/60)=1. Omission costs 7.",
            cost_weight=1,
        ),
        "C_skill_compatibility": _case(
            "C_skill_compatibility",
            (_worker("worker-a"), _worker("worker-b", skills=("b",))),
            (_job("job-a"),),
            0,
            "Only worker-a owns the required skill; worker-b's assignment is pruned.",
        ),
        "D_availability": _case(
            "D_availability",
            (
                _worker("worker-split", windows=((0, 1), (1, 2))),
                _worker("worker-continuous", windows=((0, 2),)),
            ),
            (_job("job-a", duration=2),),
            0,
            "A two-minute job crosses separate adjacent periods but fits the continuous worker.",
        ),
        "E_overlap": _case(
            "E_overlap",
            (_worker("worker-a"),),
            (_job("job-a"), _job("job-b")),
            7,
            "The jobs overlap. Assign either one and explicitly omit the other for 7.",
        ),
        "F_unassigned_override": _case(
            "F_unassigned_override",
            (_worker("worker-a"),),
            (_job("job-b", skills=("b",), priority=2, penalty=3),),
            6,
            "No worker owns skill b. Priority 2 multiplies the explicit override 3, giving 6.",
        ),
        "G_priority": _case(
            "G_priority",
            (_worker("worker-a"),),
            (_job("job-low"), _job("job-high", priority=3)),
            7,
            "The overlapping priority-3 job is assigned; omitting priority 1 costs 7 versus 21.",
        ),
        "H_workload": _case(
            "H_workload",
            (_worker("worker-a", capacity=1),),
            (_job("job-a"), _job("job-b", start=1)),
            7,
            "Touching jobs do not overlap, but their two minutes exceed capacity 1. Omit one.",
        ),
        "I_multiple_optima": _case(
            "I_multiple_optima",
            (_worker("worker-a"), _worker("worker-b")),
            (_job("job-a"),),
            0,
            "Either identical worker executes the job. Exactly two optimal schedules exist.",
        ),
        "J_combined_constraints": _case(
            "J_combined_constraints",
            (_worker("worker-a", capacity=1), _worker("worker-b", skills=("b",), capacity=2)),
            (
                _job("job-low"),
                _job("job-b", skills=("b",)),
                _job("job-high", priority=3),
            ),
            7,
            "Skills split the workers; worker-a's overlapping jobs also exceed its capacity. "
            "Keep job-high and job-b, and omit job-low.",
        ),
        "K_bounded_slack": _case(
            "K_bounded_slack",
            (_worker("worker-a", capacity=4),),
            (_job("job-short", duration=2), _job("job-long", start=2, duration=3, priority=2)),
            7,
            "Durations 2 and 3 have gcd 1. Capacity 4 requires bounded slack; keep duration 3 "
            "and omit duration 2. Residual slack is 1, including bounded-bit degeneracy.",
        ),
        "M_active_imbalance": _case(
            "M_active_imbalance",
            (_worker("worker-a", capacity=1), _worker("worker-b", capacity=1)),
            (_job("job-a"),),
            2,
            "Assigning the one-minute job gives loads (1,0) or (0,1), range 1 and cost 2. "
            "Omitting it would cost 7. Idle workers remain part of the range.",
            imbalance_weight=2,
        ),
        "N_zero_override": _case(
            "N_zero_override",
            (_worker("worker-a"),),
            (_job("job-a", penalty=0),),
            0,
            "The explicit zero omission override permits assigned and unassigned optima. "
            "Missing coverage is still invalid and receives a positive safe penalty.",
        ),
        "O_capacity_and_imbalance": _case(
            "O_capacity_and_imbalance",
            (_worker("worker-a", capacity=1), _worker("worker-b", capacity=1)),
            (_job("job-a"), _job("job-b", start=1)),
            0,
            "Two skill-compatible workers each have capacity 1. Both touching jobs can be "
            "assigned only by splitting them, giving loads (1,1) and zero range. "
            "Either split is optimal; omitting one costs 7 plus range penalty 1.",
            imbalance_weight=1,
        ),
    }


def penalty_sensitivity_case() -> tuple[SchedulingInstance, QUBOConfig]:
    """Case L: an expensive mandatory job rewards missing coverage if P is too low."""
    return _case(
        "L_unsafe_penalty",
        (_worker("worker-a", cost=6000),),
        (_job("job-a", penalty=10, mandatory=True),),
        100,
        "The mandatory one-minute assignment costs 100. Safe penalties exceed this bound. "
        "At scale 1/4 the all-zero uncovered state costs less than 100 and is invalid.",
        cost_weight=1,
        penalty_scale="1/4",
    )


def infeasible_case() -> tuple[SchedulingInstance, QUBOConfig]:
    """A separate original infeasibility case must never report equivalence PASS."""
    return _case(
        "mandatory_infeasible",
        (_worker("worker-a"),),
        (_job("job-b", skills=("b",), mandatory=True),),
        None,
        "The mandatory job has no skilled worker. No feasible M1 schedule exists.",
    )


def random_small_cases(count: int = 100) -> dict[str, tuple[SchedulingInstance, QUBOConfig]]:
    """Construct seeds 0..count-1 without dropping or resampling any outcome."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("count must be a nonnegative integer")
    cases = {}
    for seed in range(count):
        rng = random.Random(seed)
        micro = seed % 10 in (0, 1)
        worker_count = 2 if micro else rng.randrange(1, 4)
        job_count = 1 if seed % 10 == 0 else rng.randrange(1, 3 if micro else 5)
        workers = tuple(
            _worker(
                f"worker-{index}",
                skills=("shared",) if micro else (f"skill-{index}",),
                capacity=1 if seed % 10 == 0 else rng.randrange(3),
                windows=((0, 8),) if micro or rng.random() < 0.5 else ((0, 3), (5, 8)),
                cost=rng.choice((None, 1, 60, 121)),
            )
            for index in range(worker_count)
        )
        jobs = tuple(
            _job(
                f"job-{index}",
                start=rng.randrange(4 if micro else 7),
                duration=1 if micro else rng.randrange(1, 3),
                skills=("shared",) if micro else (f"skill-{rng.randrange(worker_count + 1)}",),
                priority=rng.randrange(1, 4),
                penalty=rng.choice((None, 0, 3, 11)),
                mandatory=seed % 10 == 0 and rng.random() < 0.5,
            )
            for index in range(job_count)
        )
        config = QUBOConfig(
            objective=ObjectiveWeights(
                unassigned_penalty=rng.randrange(5, 13),
                cost_weight=rng.randrange(3),
                imbalance_weight=rng.randrange(1, 4) if seed % 10 == 0 else 0,
            )
        )
        name = f"random-{seed:03d}"
        instance = SchedulingInstance(
            instance_id=f"m2-{name}",
            seed=seed,
            workers=workers,
            jobs=jobs,
            metadata={
                "source": "m2-frozen-development",
                "suite_version": RANDOM_SUITE_VERSION,
                "construction": "imbalance-micro"
                if seed % 10 == 0
                else "shared-skill-micro"
                if micro
                else "exclusive-skills",
            },
            generator_parameters={"suite_version": RANDOM_SUITE_VERSION, "seed": seed},
        )
        cases[name] = instance, config
    return cases
