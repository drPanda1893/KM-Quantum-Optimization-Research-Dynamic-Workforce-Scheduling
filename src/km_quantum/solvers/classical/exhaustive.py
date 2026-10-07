"""Small independent oracle enumerating all worker/unassigned choices.

There is deliberately no CP-SAT model, shared compatibility preprocessing or
constraint pruning here: the public validator determines candidate feasibility
and the public evaluator determines the objective.
"""

from itertools import product
from platform import python_version
from time import perf_counter
from typing import Any, Literal

from km_quantum.domain import Assignment, Schedule, SchedulingInstance, SolverConfig, SolverResult
from km_quantum.metrics import evaluate_objective, validate_schedule

IMPLEMENTATION_VERSION = "m1-v1"


def solve(instance: SchedulingInstance, config: SolverConfig | None = None) -> SolverResult:
    """Enumerate the entire choice space when it fits the configured state cap.

    The soft time limit is checked between candidates and includes validation
    and scoring. Exhaustion proves an optimum or infeasibility; a time-limited
    incumbent is merely FEASIBLE. The all-zero
    lower bound used on interrupted runs follows from nonnegative objective
    coefficients, without any reference to the CP-SAT implementation.
    """
    started = perf_counter()
    config = config or SolverConfig()
    choice_count = len(instance.workers) + 1
    state_count = 1
    for _ in instance.jobs:
        state_count *= choice_count
        if state_count > config.exhaustive_max_states:
            raise ValueError(
                "Exhaustive search exceeds exhaustive_max_states: "
                f"({len(instance.workers)} + 1) ** {len(instance.jobs)} "
                f"> {config.exhaustive_max_states}"
            )

    best_schedule: Schedule | None = None
    best_objective: int | None = None
    explored = 0
    feasible_count = 0
    complete = True
    for choices in product(range(choice_count), repeat=len(instance.jobs)):
        if perf_counter() - started >= config.time_limit_seconds:
            complete = False
            break
        # Choice zero is unassigned. All worker choices are enumerated even
        # when the public validator will reject their skills or availability.
        candidate = Schedule(
            instance_id=instance.instance_id,
            assignments=tuple(
                Assignment(worker_id=instance.workers[choice - 1].id, job_id=job.id)
                for job, choice in zip(instance.jobs, choices, strict=True)
                if choice
            ),
            unassigned_job_ids=tuple(
                job.id for job, choice in zip(instance.jobs, choices, strict=True) if not choice
            ),
        )
        explored += 1
        if not validate_schedule(instance, candidate).is_valid:
            continue
        feasible_count += 1
        objective = evaluate_objective(instance, candidate, config.objective).total
        if best_objective is None or objective < best_objective:
            best_schedule = candidate
            best_objective = objective

    metadata: dict[str, Any] = {
        "solver": "exhaustive",
        "solver_version": IMPLEMENTATION_VERSION,
        "python_version": python_version(),
        "config": config.model_dump(mode="json"),
        "state_count": state_count,
        "explored_states": explored,
        "feasible_states": feasible_count,
        "enumeration_complete": complete,
        "max_deterministic_time_applies": False,
    }
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    if best_schedule is None:
        status = "INFEASIBLE" if complete else "UNKNOWN"
        bound = None if complete else 0.0
        gap = None
    else:
        status = "OPTIMAL" if complete else "FEASIBLE"
        assert best_objective is not None
        # Preserve the exact Python integer objective even when its value cannot
        # be represented exactly by SolverResult's floating-point bound field.
        if complete:
            bound = float(best_objective) if best_objective <= (1 << 53) - 1 else None
        else:
            bound = 0.0
        if complete and bound is None:
            metadata["bound_omitted_reason"] = "integer optimum exceeds exact float range"
        gap = 0.0 if complete else best_objective / max(1, abs(best_objective))
        best_schedule = best_schedule.model_copy(
            update={"objective_value": best_objective, "solver_metadata": metadata.copy()}
        )

    return SolverResult(
        status=status,
        schedule=best_schedule,
        objective_value=best_objective,
        runtime_ms=(perf_counter() - started) * 1000,
        proven_optimal=complete and best_schedule is not None,
        best_bound=bound,
        optimality_gap=gap,
        solver_metadata=metadata,
    )
